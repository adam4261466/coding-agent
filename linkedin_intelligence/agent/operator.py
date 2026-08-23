"""Agent operator: QUEUE -> PERMISSION GATE -> OPERATOR -> MEMORY.

Replaces the LLM planner loop. Every decision here is deterministic code;
the LLM is only invoked inside language tasks (message copy, reply
classification, objection drafts). The human decides WHO may be contacted
and WHAT may be done via permission records (see permissions.py); the
operator reads them and obeys. A missing record = manual review = hands off.

One tick processes exactly ONE gated action for exactly ONE person:
  1. QUEUE       - ProspectQueue.allowed() in priority order
  2. PLAN        - pure function of (state, events, permissions) -> one step
  3. GATE        - permission check, then rate limit
  4. OPERATE     - browser action / outreach pipeline (LLM = language only)
  5. MEMORY      - event + action log + snapshot after every attempt
"""

import time
from collections import deque
from datetime import datetime, timezone

from ..automation.memory.memory_service import MemoryService
from ..automation.actions.browser_executor import BrowserExecutor
from ..automation.actions.linkedin_actions import (
    observe_profile, observe_conversation,
    send_connection_request, send_message as send_message_action)
from ..automation.monitoring.handoff import check_rate_limit
from ..outreach.approval import approve
from ..outreach.campaign import next_batch
from ..outreach.pipeline import produce_message
from ..outreach.conversation import record_reply, prepare_objection_draft
from ..outreach.sequence import mark_sent, due_follow_ups
from ..outreach.state_machine import transition
from .permissions import allows, normalize
from .queue import ProspectQueue

DEFAULT_MODEL = "qwen3.5:0.8b"
DEFAULT_BASE_URL = "http://localhost:11434"


class AgentOperator:
    def __init__(self, store, memory: MemoryService = None,
                 browser: BrowserExecutor = None,
                 model: str = DEFAULT_MODEL,
                 base_url: str = DEFAULT_BASE_URL,
                 dry_run: bool = False,
                 max_cycles: int = None,
                 cycle_delay: int = 60):
        self.store = store
        self.memory = memory or MemoryService(
            store.path.replace(".db", "_agent.db"))
        self.browser = browser or BrowserExecutor(model=model,
                                                  base_url=base_url)
        self.model = model
        self.base_url = base_url
        self.dry_run = dry_run
        self.max_cycles = max_cycles
        self.cycle_delay = cycle_delay
        self.queue = ProspectQueue(store)
        self._cycles = 0
        self.paused = False
        self.log_lines = deque(maxlen=1000)
        # Circuit breaker: (prospect_id, action) -> consecutive failed
        # attempts. Stops the operator from hammering a broken action
        # (e.g. Ollama down) every tick; after FAIL_LIMIT it hands the
        # person to a human instead.
        self._fail_streak = {}
        self.FAIL_LIMIT = 3

    # ---- live log (dashboard reads this) ----

    def log(self, msg: str):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        self.log_lines.append(line)
        print(line, flush=True)

    def recent_log(self, n: int = 200) -> list:
        lines = list(self.log_lines)
        return lines[-n:]

    # ---- main loop ----

    def run(self):
        self.memory.record_event("agent_start", data={
            "engine": "operator", "dry_run": self.dry_run})
        self.log(f"operator started (dry_run={self.dry_run})")
        try:
            while True:
                if self.paused:
                    time.sleep(1)
                    continue
                self._cycles += 1
                if self.max_cycles and self._cycles > self.max_cycles:
                    self.log(f"reached max cycles ({self.max_cycles})")
                    break
                result = self.tick()
                if result.get("status") == "idle":
                    time.sleep(self.cycle_delay)
                else:
                    time.sleep(2)
        except KeyboardInterrupt:
            self.log("interrupted by user")
        finally:
            self.memory.record_event("agent_stop",
                                     data={"cycles": self._cycles})
            self.log(f"stopped after {self._cycles} ticks")
            try:
                self.browser.close()
            except Exception:
                pass

    def run_once(self) -> dict:
        """One tick: find the first person with work, do ONE gated action."""
        self._cycles += 1
        return self.tick()

    def tick(self) -> dict:
        for entry in self.queue.allowed():
            person = entry["prospect"]
            record = entry["record"]
            step = self.plan_step(person, record)
            if not step:
                continue
            return self.execute(person, record, step)
        summary = self.queue.summary()
        self.log(f"idle - allowed={summary['allowed']} "
                 f"manual={summary['manual']} blocked={summary['blocked']}, "
                 f"nobody has a permitted next action")
        return {"status": "idle"}

    # ---- deterministic planner (no LLM) ----

    def plan_step(self, person: dict, record) -> dict | None:
        """Pure function of state + permissions -> the single next step.
        Order: PROFILE -> CONNECT -> MESSAGE -> REPLY -> FOLLOW_UP."""
        pid = person["prospect_id"]
        ctx = self.memory.get_prospect_context(pid)
        rel = (ctx.get("state") or {}).get("relationship") or {}
        conn = rel.get("connection_status") or \
            person.get("connection_status") or "unknown"
        if conn == "1st_degree":
            conn = "connected"

        recent = ctx.get("recent_events") or []

        # A person waiting on a human (unresolved handoff) is never
        # re-planned - the human must clear it first (human_cleared event).
        if self.memory.events.count_events(pid, "handoff_requested") > \
                self.memory.events.count_events(pid, "human_cleared"):
            return None

        def count(event_type):
            return sum(1 for e in recent if e.get("type") == event_type)

        observed = any(
            e.get("type") == "profile_observed" and
            (e.get("data") or {}).get("success", True)
            for e in recent)
        connection_sent = count("connection_sent") > 0
        messages_sent = count("message_sent")
        replies_seen = count("reply_detected")
        replies_done = count("reply_processed")

        if not observed and allows(record, "observe_profile"):
            return {"action": "observe_profile"}

        if allows(record, "send_connection_request") and \
                conn != "connected" and not connection_sent and \
                conn != "pending":
            return {"action": "send_connection_request"}

        if allows(record, "send_message") and conn == "connected" and \
                messages_sent == 0 and replies_seen == replies_done:
            return {"action": "send_message"}

        if replies_seen > replies_done and allows(record, "reply"):
            return {"action": "reply"}

        if allows(record, "follow_up") and messages_sent > 0 and \
                self._follow_up_due(pid):
            return {"action": "follow_up"}

        return None

    def _follow_up_due(self, prospect_id: str) -> bool:
        for campaign in self._campaigns():
            for cp in due_follow_ups(self.store, campaign):
                if cp["prospect_id"] == prospect_id:
                    return True
        return False

    def _campaigns(self) -> list:
        return [c for c in (self.store.campaigns(status="active") +
                            self.store.campaigns(status="synced"))]

    def _campaign_for(self, prospect_id: str):
        """The active campaign row for this prospect, moving it into the
        message pipeline if it is still sitting at CAMPAIGN_ASSIGNED."""
        cps = [cp for cp in self.store.campaign_prospects(
            prospect_id=prospect_id)
            if cp.get("status") not in ("DO_NOT_CONTACT", "REJECTED",
                                        "UNQUALIFIED", "CUSTOMER")]
        if not cps:
            return None, None
        cps.sort(key=lambda c: (c.get("priority") or 0), reverse=True)
        cp = cps[0]
        campaign = self.store.get_campaign(cp["campaign_id"])
        if campaign and campaign.get("status") not in ("active", "synced"):
            return None, None
        if cp.get("status") == "CAMPAIGN_ASSIGNED":
            batch = next_batch(self.store, campaign, limit=0)
            cp = next((b for b in batch
                       if b["prospect_id"] == prospect_id), None) or cp
        return campaign, cp

    # ---- gate + execution ----

    def execute(self, person: dict, record, step: dict) -> dict:
        pid = person["prospect_id"]
        name = person.get("full_name") or pid
        action = step["action"]
        record = normalize(record)

        if record["blocked"] or record["permission"] != "allowed" or \
                not allows(record, action):
            self.memory.record_event(
                "permission_blocked", pid,
                data={"action": action,
                      "permission": record["permission"]},
                source="gate")
            self.log(f"GATE {name}: {action} blocked by permissions")
            return {"status": "blocked_by_permission", "person": name}

        if not check_rate_limit(self.memory.events, action, pid):
            self.log(f"RATE {name}: {action} rate limited")
            return {"status": "rate_limited", "person": name}

        self.log(f"WORK {name}: {action}")
        if self.dry_run:
            self.memory.record_event(
                "dry_run", pid, data={"action": action}, source="operator")
            return {"status": "dry_run", "person": name, "action": action}

        handler = {
            "observe_profile": self._do_observe_profile,
            "send_connection_request": self._do_connect,
            "send_message": self._do_send_message,
            "reply": self._do_reply,
            "follow_up": self._do_follow_up,
        }[action]
        result = handler(person, record)
        result.update({"person": name, "action": action})

        streak_key = (pid, action)
        if result["status"] in ("failed", "send_failed", "error"):
            self._fail_streak[streak_key] = \
                self._fail_streak.get(streak_key, 0) + 1
            if self._fail_streak[streak_key] >= self.FAIL_LIMIT:
                self.memory.record_event(
                    "handoff_requested", pid,
                    data={"reason": "repeated_failures",
                          "action": action,
                          "attempts": self._fail_streak[streak_key]})
                self._fail_streak[streak_key] = 0
                self.log(f"HOLD {name}: {action} failed "
                         f"{self.FAIL_LIMIT}x in a row - manual review")
                result["status"] = "handed_off"
                return result
        else:
            self._fail_streak[streak_key] = 0

        self.memory.save_snapshot(pid)
        return result

    # ---- individual actions ----

    def _do_observe_profile(self, person, record) -> dict:
        pid = person["prospect_id"]
        try:
            result = observe_profile(self.browser, person)
            ok = bool(result.get("success"))
            self.memory.events.record_action("observe_profile", pid, success=ok)
            findings = result.get("observations") or []
            data = {"success": ok,
                    "findings": [str(f)[:300] for f in findings[:10]],
                    "summary": str(result.get("summary", ""))[:500]}
            for f in findings:
                if isinstance(f, dict):
                    for key in ("name", "company", "role"):
                        if f.get(key):
                            data[key] = f[key]
                    if any(f.get(k) for k in ("name", "company", "role")):
                        self.memory.record_fact(
                            pid, "observed",
                            str(f.get("claim") or f)[:200],
                            confidence=float(f.get("confidence") or 0.8),
                            source="linkedin_observation",
                            evidence=str(f.get("evidence", ""))[:200])
            self.memory.record_event("profile_observed", pid, data=data,
                                     source="browser", confidence=0.9)
            self.log(f"DONE {person.get('full_name', pid)}: profile observed "
                     f"(success={ok})")
            return {"status": "executed" if ok else "failed"}
        except Exception as exc:
            self.memory.events.record_action("observe_profile", pid,
                                             success=False)
            self.memory.record_event("error", pid,
                                     data={"error": str(exc),
                                           "action": "observe_profile"})
            self.log(f"ERROR {pid}: observe_profile {exc}")
            return {"status": "error", "error": str(exc)}

    def _do_connect(self, person, record) -> dict:
        pid = person["prospect_id"]
        campaign, cp = self._campaign_for(pid)
        try:
            result = send_connection_request(self.browser, person)
            ok = bool(result.get("success"))
            self.memory.events.record_action("send_connection_request", pid,
                                             success=ok)
            self.memory.record_event(
                "connection_sent" if ok else "connection_failed",
                pid, campaign_id=campaign["campaign_id"] if campaign else None,
                data={"success": ok,
                      "description": str(result.get("description", ""))[:300]},
                source="browser", confidence=0.95)
            if ok and cp and campaign:
                try:
                    transition(self.store, cp, "SENT",
                               event="connection_request_sent")
                except ValueError:
                    pass
            self.log(f"DONE {person.get('full_name', pid)}: connection "
                     f"request {'sent' if ok else 'FAILED'}")
            return {"status": "sent" if ok else "send_failed"}
        except Exception as exc:
            self.memory.events.record_action("send_connection_request", pid,
                                             success=False)
            self.memory.record_event("error", pid,
                                     data={"error": str(exc),
                                           "action": "connect"})
            self.log(f"ERROR {pid}: connect {exc}")
            return {"status": "error", "error": str(exc)}

    def _produce_and_send(self, person, campaign, cp, kind="initial") -> dict:
        """Language pipeline: generate (LLM) -> validate (deterministic) ->
        approve (permission already granted) -> send (browser). Any validation
        failure routes to manual review instead of sending."""
        pid = person["prospect_id"]
        pending = [m for m in self.store.messages_for(
            prospect_id=pid, campaign_id=campaign["campaign_id"])
            if m.get("status") == "approved_to_send"]
        if pending:
            msg = pending[-1]
        else:
            try:
                msg = produce_message(self.store, campaign, cp,
                                      model=self.model,
                                      base_url=self.base_url)
            except Exception as exc:
                self.memory.record_event(
                    "handoff_requested", pid, campaign["campaign_id"],
                    data={"reason": "generation_failed", "error": str(exc)})
                self.log(f"HOLD {person.get('full_name', pid)}: generation "
                         f"failed ({exc}) - manual review")
                return {"status": "handed_off"}
        if msg.get("status") not in ("approved", "approved_to_send"):
            self.memory.record_event(
                "handoff_requested", pid, campaign["campaign_id"],
                data={"reason": "validation_failed",
                      "validation": msg.get("validation", [])})
            self.log(f"HOLD {person.get('full_name', pid)}: message failed "
                     f"validation - manual review")
            return {"status": "handed_off"}
        if msg.get("status") == "approved":
            approve(self.store, msg["message_id"])
            self.memory.record_event("message_approved", pid,
                                     campaign["campaign_id"],
                                     data={"message_id": msg["message_id"]},
                                     source="operator", confidence=1.0)
        text = msg.get("text", "")
        result = send_message_action(self.browser, person, message_text=text)
        ok = bool(result.get("success"))
        self.memory.events.record_action("send_message", pid, success=ok)
        self.memory.record_event(
            "message_sent", pid, campaign["campaign_id"],
            data={"message_id": msg["message_id"], "success": ok,
                  "kind": kind,
                  "description": str(result.get("description", ""))[:300]},
            source="browser", confidence=0.95)
        if ok:
            mark_sent(self.store, cp)
        self.log(f"DONE {person.get('full_name', pid)}: {kind} message "
                 f"{'sent' if ok else 'FAILED'}")
        return {"status": "sent" if ok else "send_failed"}

    def _do_send_message(self, person, record) -> dict:
        pid = person["prospect_id"]
        campaign, cp = self._campaign_for(pid)
        if not campaign or not cp:
            self.memory.record_event(
                "handoff_requested", pid,
                data={"reason": "no_campaign_assignment"})
            self.log(f"HOLD {person.get('full_name', pid)}: no campaign "
                     f"assignment - manual review")
            return {"status": "handed_off"}
        try:
            return self._produce_and_send(person, campaign, cp, kind="first")
        except Exception as exc:
            self.memory.events.record_action("send_message", pid,
                                             success=False)
            self.memory.record_event("error", pid,
                                     data={"error": str(exc),
                                           "action": "send_message"})
            self.log(f"ERROR {pid}: send_message {exc}")
            return {"status": "error", "error": str(exc)}

    def _detect_inbound(self, observe_result: dict) -> str | None:
        """Best-effort: did the conversation show an inbound/unread message?
        Returns the observation summary to classify, or None."""
        blob = " ".join(str(x) for x in (observe_result.get("observations")
                                         or [])).lower() + " " + \
            str(observe_result.get("summary", "")).lower()
        markers = ("unread", "new message", "replied", "reply from",
                   "said:", "wrote:")
        return blob.strip() if any(m in blob for m in markers) else None

    def _do_reply(self, person, record) -> dict:
        """Observe the thread; if something came in, classify it (LLM),
        advance the state machine deterministically, then either draft an
        objection response into manual review or answer with the same
        produce->validate->approve->send pipeline."""
        pid = person["prospect_id"]
        campaign, cp = self._campaign_for(pid)
        try:
            seen = observe_conversation(self.browser, person)
            ok = bool(seen.get("success"))
            self.memory.events.record_action("observe_conversation", pid,
                                             success=ok)
            inbound = self._detect_inbound(seen) if ok else None
            if not inbound:
                self.memory.record_event(
                    "reply_processed", pid,
                    data={"result": "no_inbound_found"},
                    source="operator")
                self.log(f"DONE {person.get('full_name', pid)}: no reply "
                         f"found in thread")
                return {"status": "no_inbound"}
            classification = record_reply(
                self.store, person, campaign,
                inbound[:2000], model=self.model) if campaign else {}
            self.memory.record_event("reply_classified", pid,
                                     campaign["campaign_id"] if campaign else None,
                                     data=classification, source="operator")
            intent = classification.get("intent", "unknown")
            self.log(f"REPLY {person.get('full_name', pid)}: intent="
                     f"{intent}")
            if intent == "objection":
                prepare_objection_draft(
                    self.store, person, campaign, classification,
                    model=self.model, base_url=self.base_url)
                self.memory.record_event(
                    "handoff_requested", pid,
                    campaign["campaign_id"] if campaign else None,
                    data={"reason": "objection_draft_needs_review"})
                self.log(f"HOLD {person.get('full_name', pid)}: objection "
                         f"draft queued for manual review")
                return {"status": "drafted_for_review"}
            self.memory.record_event("reply_processed", pid,
                                     data={"intent": intent},
                                     source="operator")
            if intent in ("opt_out", "not_interested"):
                return {"status": "closed", "intent": intent}
            if campaign and cp:
                return self._produce_and_send(person, campaign, cp,
                                              kind="reply")
            return {"status": "classified", "intent": intent}
        except Exception as exc:
            self.memory.events.record_action("observe_conversation", pid,
                                             success=False)
            self.memory.record_event("error", pid,
                                     data={"error": str(exc),
                                           "action": "reply"})
            self.log(f"ERROR {pid}: reply {exc}")
            return {"status": "error", "error": str(exc)}

    def _do_follow_up(self, person, record) -> dict:
        pid = person["prospect_id"]
        campaign, cp = self._campaign_for(pid)
        if not campaign or not cp:
            self.memory.record_event(
                "handoff_requested", pid,
                data={"reason": "no_campaign_assignment"})
            return {"status": "handed_off"}
        try:
            try:
                transition(self.store, cp, "FOLLOWUP_ELIGIBLE",
                           event="operator_follow_up")
                cp = self.store.get_campaign_prospect(campaign["campaign_id"],
                                                      pid) or cp
            except ValueError:
                pass
            return self._produce_and_send(person, campaign, cp,
                                          kind="follow_up")
        except Exception as exc:
            self.memory.events.record_action("follow_up", pid, success=False)
            self.memory.record_event("error", pid,
                                     data={"error": str(exc),
                                           "action": "follow_up"})
            self.log(f"ERROR {pid}: follow_up {exc}")
            return {"status": "error", "error": str(exc)}
