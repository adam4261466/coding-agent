"""Agent executor: translates planner decisions into real actions.

This is the bridge between the LLM planner (which decides WHAT to do)
and the browser/actions layer (which does it). It:
  1. Receives an action plan from the planner
  2. Records the decision in the event store
  3. Checks rate limits and eligibility
  4. Delegates to the appropriate action function
  5. Records the result as events
  6. Updates memory state
  7. Takes a snapshot for crash recovery

CHANGES FROM THE ORIGINAL:
  - _execute_send_message / _execute_browser_action now call
    record_action() AFTER the outcome is known, passing success=... so
    the rate limiter's cooldown logic (which excludes failures) is
    accurate instead of always recording success=True by default.
  - _execute_retry_send no longer DELETEs rows from the events table.
    That was a workaround for the old rate limiter, which blocked
    forever once any event existed; it also violated this store's own
    "events are never modified or deleted" invariant, and its LIKE
    patterns ('%false%') were broad enough to catch unrelated rows.
    With the real rate limiter (see automation/monitoring/handoff.py),
    a failed send doesn't block a retry - retry_send can just retry.
"""

from ..outreach.state_machine import transition
from ..outreach.pipeline import produce_message, register_reply
from ..outreach.approval import approval_queue, approve, reject
from ..outreach.sequence import mark_sent, make_follow_up_eligible
from ..automation.actions.linkedin_actions import ACTION_MAP
from ..automation.monitoring.handoff import check_rate_limit, check_approval_required
from ..automation.memory.memory_service import MemoryService


def execute_plan(store, memory: MemoryService, browser,
                 prospect: dict, campaign: dict, plan: dict,
                 model: str = None, base_url: str = None) -> dict:
    """Execute one action plan. Records everything in memory."""
    action = plan.get("action", "wait")
    params = plan.get("params", {})
    if model and not params.get("model"):
        params["model"] = model
    if base_url and not params.get("base_url"):
        params["base_url"] = base_url
    prospect_id = prospect["prospect_id"]
    campaign_id = campaign["campaign_id"]

    memory.record_event("agent_decision", prospect_id, campaign_id,
                        data={"action": action, "reason": plan.get("reason"),
                              "params": params},
                        source="planner")

    if action == "wait":
        return {"status": "skipped", "reason": plan.get("reason")}

    if action == "skip":
        try:
            cp = store.get_campaign_prospect(campaign_id, prospect_id)
            if cp:
                transition(store, cp, "DO_NOT_CONTACT",
                           event="agent_skip", note=plan.get("reason"))
        except ValueError:
            pass
        memory.record_event("agent_skip", prospect_id, campaign_id,
                            data={"reason": plan.get("reason")})
        return {"status": "skipped", "reason": plan.get("reason")}

    if action == "handoff":
        memory.record_event("handoff_requested", prospect_id, campaign_id,
                            data={"reason": plan.get("reason", "agent_uncertain")})
        return {"status": "handed_off"}

    if action == "produce_message":
        return _execute_produce_message(store, memory, prospect, campaign,
                                        plan, params)

    if action == "approve_message":
        return _execute_approve_message(store, memory, prospect, campaign,
                                        plan, params)

    if action == "send_message":
        return _execute_send_message(store, memory, browser,
                                     prospect, campaign, plan, params)

    if action == "retry_send":
        return _execute_retry_send(store, memory, browser,
                                   prospect, campaign, plan, params)

    if action == "follow_up":
        return _execute_follow_up(store, memory, prospect, campaign,
                                  plan, params)

    if action == "research_prospect":
        return _execute_research(store, memory, browser,
                                 prospect, campaign, plan, params)

    if action == "qualify_prospect":
        return _execute_qualify(store, memory, prospect, campaign,
                                plan, params)

    if action in ACTION_MAP:
        return _execute_browser_action(store, memory, browser,
                                       prospect, campaign, action, plan, params)

    memory.record_event("error", prospect_id, campaign_id,
                        data={"error": f"unknown action: {action}"})
    return {"status": "error", "error": f"unknown action: {action}"}


def _execute_produce_message(store, memory, prospect, campaign,
                             plan, params) -> dict:
    prospect_id = prospect["prospect_id"]
    campaign_id = campaign["campaign_id"]

    if not check_rate_limit(memory.events, "produce_message", prospect_id):
        return {"status": "rate_limited"}

    if check_approval_required(campaign):
        try:
            msg = produce_message(store, campaign,
                                  store.get_campaign_prospect(campaign_id,
                                                              prospect_id),
                                  model=params.get("model"),
                                  base_url=params.get("base_url"))
            memory.events.record_action("produce_message", prospect_id)
            memory.record_event("message_generated", prospect_id, campaign_id,
                                data={"message_id": msg.get("message_id"),
                                      "status": msg.get("status")})
            memory.tasks.create_task(
                prospect_id, "approve_message", campaign_id,
                initial_steps=["human_approval"])
            return {"status": "produced", "message": msg}
        except Exception as exc:
            memory.record_event("error", prospect_id, campaign_id,
                                data={"error": str(exc),
                                      "action": "produce_message"})
            return {"status": "error", "error": str(exc)}
    else:
        memory.record_event("handoff_requested", prospect_id, campaign_id,
                            data={"reason": "approval_required_no_auto"})
        return {"status": "handed_off"}


def _execute_approve_message(store, memory, prospect, campaign,
                             plan, params) -> dict:
    """Auto-approve the latest validated message for this prospect."""
    prospect_id = prospect["prospect_id"]
    campaign_id = campaign["campaign_id"]

    msgs = store.messages_for(prospect_id=prospect_id, campaign_id=campaign_id)
    pending = [m for m in msgs if m.get("status") in ("generated", "pending_review")]
    already_approved = [m for m in msgs if m.get("status") == "approved_to_send"]

    if already_approved:
        from ..outreach.state_machine import transition
        try:
            cp = store.get_campaign_prospect(campaign_id, prospect_id)
            if cp and cp.get("status") != "APPROVED_TO_SEND":
                transition(store, cp, "APPROVED_TO_SEND",
                           event="already_approved",
                           note=already_approved[-1].get("message_id"))
        except ValueError:
            pass
        return {"status": "already_approved",
                "message_id": already_approved[-1].get("message_id")}
    if not pending:
        return {"status": "skipped", "reason": "no message to approve"}

    msg = pending[-1]
    try:
        approve(store, msg["message_id"])
        memory.events.record_action("approve_message", prospect_id)
        memory.record_event("message_approved", prospect_id, campaign_id,
                            data={"message_id": msg["message_id"]})
        active = memory.tasks.active_tasks(prospect_id=prospect_id)
        for t in active:
            if t.get("goal") == "approve_message":
                memory.tasks.complete_step(t["task_id"], "human_approval")
                break
        return {"status": "approved", "message_id": msg["message_id"]}
    except Exception as exc:
        memory.record_event("error", prospect_id, campaign_id,
                            data={"error": str(exc), "action": "approve_message"})
        return {"status": "error", "error": str(exc)}


def _execute_send_message(store, memory, browser, prospect, campaign,
                          plan, params) -> dict:
    """Send the approved message via LinkedIn."""
    prospect_id = prospect["prospect_id"]
    campaign_id = campaign["campaign_id"]

    if not check_rate_limit(memory.events, "send_message", prospect_id):
        return {"status": "rate_limited"}

    try:
        approved = [m for m in store.messages_for(
            prospect_id=prospect_id, campaign_id=campaign_id)
            if m.get("status") == "approved_to_send"]
        if not approved:
            return {"status": "skipped", "reason": "no approved message"}

        msg = approved[-1]
        message_text = msg.get("text", "")

        from ..automation.actions.linkedin_actions import send_message
        result = send_message(browser, prospect, message_text=message_text)

        browser_reported_success = result.get("success", False)
        description = result.get("description", "")

        if not browser_reported_success and not result.get("error"):
            browser_reported_success = any(
                kw in description.lower()
                for kw in ("sent", "send", "click", "message"))

        # Record AFTER the outcome is known: a failed attempt is logged as
        # success=False so it doesn't block an immediate retry (see
        # handoff.check_rate_limit), but still counts toward the daily cap.
        memory.events.record_action("send_message", prospect_id,
                                    success=browser_reported_success)

        memory.record_event("message_sent", prospect_id, campaign_id,
                            data={"message_id": msg["message_id"],
                                  "success": browser_reported_success,
                                  "description": description[:300]},
                            source="browser", confidence=0.95)

        from ..outreach.sequence import mark_sent
        if browser_reported_success:
            cp = store.get_campaign_prospect(campaign_id, prospect_id)
            if cp:
                mark_sent(store, cp)

        return {"status": "sent" if browser_reported_success else "send_failed",
                "result": result}
    except Exception as exc:
        memory.events.record_action("send_message", prospect_id, success=False)
        memory.record_event("error", prospect_id, campaign_id,
                            data={"error": str(exc), "action": "send_message"})
        return {"status": "error", "error": str(exc)}


def _execute_retry_send(store, memory, browser, prospect, campaign,
                        plan, params) -> dict:
    """Retry sending the approved message.

    Previously this deleted rows from the (supposedly immutable) events
    table to work around a rate limiter that blocked forever after any
    event existed. That's no longer necessary: check_rate_limit excludes
    failed attempts from the per-prospect cooldown, so a genuine send
    failure can be retried right away without touching history.
    """
    prospect_id = prospect["prospect_id"]
    campaign_id = campaign["campaign_id"]

    memory.record_event("retry_send", prospect_id, campaign_id,
                        data={"reason": plan.get("reason", "retrying failed send")})

    return _execute_send_message(store, memory, browser,
                                 prospect, campaign, plan, params)


def _execute_follow_up(store, memory, prospect, campaign,
                       plan, params) -> dict:
    prospect_id = prospect["prospect_id"]
    campaign_id = campaign["campaign_id"]

    if not check_rate_limit(memory.events, "follow_up", prospect_id):
        return {"status": "rate_limited"}

    try:
        cp = store.get_campaign_prospect(campaign_id, prospect_id)
        if cp:
            transition(store, cp, "FOLLOWUP_ELIGIBLE",
                       event="agent_follow_up", note=plan.get("reason"))
        msg = produce_message(store, campaign, cp,
                              model=params.get("model"),
                              base_url=params.get("base_url"))
        memory.events.record_action("follow_up", prospect_id)
        memory.record_event("message_generated", prospect_id, campaign_id,
                            data={"message_id": msg.get("message_id"),
                                  "status": msg.get("status"),
                                  "type": "follow_up"})
        return {"status": "produced", "message": msg}
    except Exception as exc:
        memory.record_event("error", prospect_id, campaign_id,
                            data={"error": str(exc), "action": "follow_up"})
        return {"status": "error", "error": str(exc)}


def _execute_browser_action(store, memory, browser,
                            prospect, campaign, action_name, plan, params) -> dict:
    prospect_id = prospect["prospect_id"]
    campaign_id = campaign["campaign_id"]

    if not check_rate_limit(memory.events, action_name, prospect_id):
        return {"status": "rate_limited"}

    action_fn = ACTION_MAP.get(action_name)
    if not action_fn:
        return {"status": "error", "error": f"no action fn for {action_name}"}

    import inspect
    sig = inspect.signature(action_fn)
    valid_params = {k: v for k, v in params.items() if k in sig.parameters}

    try:
        result = action_fn(browser, prospect, **valid_params)
        succeeded = bool(result.get("success"))

        # Record AFTER the outcome is known (see _execute_send_message).
        memory.events.record_action(action_name, prospect_id, success=succeeded)

        event_type = {
            "send_connection_request": "connection_sent",
            "send_message": "message_sent",
            "observe_profile": "profile_observed",
            "view_profile": "profile_observed",
            "endorse_skill": "skill_endorsed",
        }.get(action_name, "action_executed")

        memory.record_event(event_type, prospect_id, campaign_id,
                            data={"success": result.get("success"),
                                  "description": result.get("description", "")[:500]},
                            source="browser", confidence=0.95)

        if succeeded and action_name == "send_connection_request":
            try:
                cp = store.get_campaign_prospect(campaign_id, prospect_id)
                if cp and cp.get("status") == "CAMPAIGN_ASSIGNED":
                    transition(store, cp, "SENT",
                               event="connection_request_sent")
            except ValueError:
                pass

        return {"status": "executed", "result": result}
    except Exception as exc:
        memory.events.record_action(action_name, prospect_id, success=False)
        memory.record_event("error", prospect_id, campaign_id,
                            data={"error": str(exc), "action": action_name})
        return {"status": "error", "error": str(exc)}


def _execute_research(store, memory, browser, prospect, campaign,
                      plan, params) -> dict:
    """Run Phase 2 browser research on a prospect."""
    prospect_id = prospect["prospect_id"]
    campaign_id = campaign["campaign_id"]

    if not check_rate_limit(memory.events, "research_prospect", prospect_id):
        return {"status": "rate_limited"}

    try:
        from ..research.campaign import build_research_tasks
        from ..research.agent import run_research
        from ..research.evidence import record_findings

        tasks = build_research_tasks(store, selected_ids=[prospect_id],
                                     batch_type="acquisition")
        if not tasks:
            return {"status": "skipped", "reason": "no research tasks generated"}

        all_findings = []
        for task in tasks:
            result = run_research(
                task, prospect,
                model=params.get("model", "gemma4:31b-cloud"),
                base_url=params.get("base_url", "http://localhost:11434"),
                store=store,
                offline=params.get("offline", False))
            if result.get("findings"):
                all_findings.extend(result["findings"])

        memory.events.record_action("research_prospect", prospect_id,
                                    success=bool(all_findings))
        memory.record_event("profile_observed", prospect_id, campaign_id,
                            data={"findings_count": len(all_findings),
                                  "tasks": len(tasks)},
                            source="browser", confidence=0.95)

        for f in all_findings:
            memory.record_fact(prospect_id, "observed", f.get("claim", "")[:200],
                               confidence=f.get("confidence", 0.8),
                               source="linkedin_observation",
                               evidence=f.get("evidence", "")[:200])

        return {"status": "researched", "findings": len(all_findings)}
    except Exception as exc:
        memory.events.record_action("research_prospect", prospect_id, success=False)
        memory.record_event("error", prospect_id, campaign_id,
                            data={"error": str(exc), "action": "research_prospect"})
        return {"status": "error", "error": str(exc)}


def _execute_qualify(store, memory, prospect, campaign,
                     plan, params) -> dict:
    """Run Phase 2 qualification on a prospect."""
    prospect_id = prospect["prospect_id"]
    campaign_id = campaign["campaign_id"]

    try:
        from ..research.qualifier import qualify, validate_qualification
        from ..research.evidence import evidence_block

        evidence = evidence_block(store, prospect)
        q = qualify(
            store, prospect,
            model=params.get("model", "gemma4:31b-cloud"),
            base_url=params.get("base_url", "http://localhost:11434"),
            evidence=evidence,
            research_mode=params.get("research_mode", "live_browser"))

        store.save_qualification({
            "prospect_id": prospect_id,
            "fit_score": q.get("fit_score"),
            "problem_fit_score": q.get("problem_fit_score"),
            "confidence": q.get("confidence"),
            "research_mode": q.get("research_mode"),
            "method": q.get("method"),
            "reason": q.get("reason"),
            "evidence_used": q.get("evidence_used", []),
            "uncertainties": q.get("uncertainties", []),
            "recommended_next_action": q.get("recommended_next_action"),
            "contradictions": q.get("contradictions", []),
            "research_stage": "agent",
            "model": q.get("model"),
        })

        action = q.get("recommended_next_action", "RESEARCH_MORE")
        if action in ("READY_FOR_OUTREACH", "READY_FOR_HUMAN_REVIEW"):
            store.set_status(prospect_id, "ready_for_outreach")
            memory.record_event("qualified", prospect_id, campaign_id,
                                data={"score": q.get("fit_score"),
                                      "action": action, "qualified": True})
            memory.record_fact(prospect_id, "qualified", "true",
                               confidence=q.get("confidence", 0.5),
                               source="llm_qualification")
        elif action == "DO_NOT_CONTACT":
            store.set_status(prospect_id, "do_not_contact")
            memory.record_event("qualified", prospect_id, campaign_id,
                                data={"score": q.get("fit_score"),
                                      "action": action, "qualified": False})
        else:
            memory.record_event("qualified", prospect_id, campaign_id,
                                data={"score": q.get("fit_score"),
                                      "action": action, "qualified": False})

        memory.events.record_action("qualify_prospect", prospect_id)
        return {"status": "qualified", "qualification": q}
    except Exception as exc:
        memory.record_event("error", prospect_id, campaign_id,
                            data={"error": str(exc), "action": "qualify_prospect"})
        return {"status": "error", "error": str(exc)}
