"""LLM planner: decides the next action for each prospect.

The planner receives a complete context from the memory service and
decides what to do next. It returns a structured action plan — it never
performs the action itself.

The planner sees:
  - Current state (what is true NOW)
  - Facts (what we know with confidence)
  - Recent events (what happened)
  - Active task progress (what we are trying to do)
  - Product journey (signup, activation status)
  - Conversation summary (last intent, objection)
"""

import json
import requests

DEFAULT_MODEL = "qwen3.5:0.8b"
DEFAULT_BASE_URL = "http://localhost:11434"

PLANNER_SYSTEM_PROMPT = """You are a LinkedIn outreach PLANNER. You decide
what action to take next for a prospect based on their complete context.

You have these actions available:
- research_prospect: Run browser research on the prospect's LinkedIn profile (Phase 2)
- qualify_prospect: Run LLM qualification on gathered evidence (Phase 2)
- observe_profile: Navigate to the prospect's LinkedIn profile and gather information
- send_connection_request: Send a connection request (with optional note)
- send_message: Send a message in an existing conversation
- produce_message: Generate a new outreach message (goes to human approval)
- approve_message: Approve a generated message and mark it sent
- follow_up: Generate a follow-up message (goes to human approval)
- endorse_skill: Endorse a skill on the prospect's profile
- view_profile: Simply view the profile (triggers notification)
- wait: Do nothing, wait for the prospect to respond
- skip: Mark prospect as not worth pursuing
- handoff: Request human intervention

WORKFLOW:
1. If the prospect has no facts/research — use research_prospect first
2. After research — use qualify_prospect to score them
3. After qualification — use produce_message or send_connection_request
4. If message is approved (APPROVED_TO_SEND) — use send_message
5. If they reply — classify and decide next step
6. If follow_up is due — use follow_up

CRITICAL RULES:
- NEVER send a message asking about signup if they already signed up.
- NEVER send a connection request if they are already connected.
- NEVER ask about topics the facts already answer.
- NEVER plan research_prospect if a research_prospect action appears in RECENT EVENTS — whether recorded as dry_run, action, or any other event type. Research was already planned; move to produce_message.
- NEVER plan observe_profile if it already appears in RECENT EVENTS — observation was already planned; use produce_message instead.
- For MESSAGE_PENDING: use produce_message (or research_prospect if no facts exist).
- For MESSAGE_REVIEW: use approve_message (the agent auto-approves).
- For APPROVED_TO_SEND: use send_message.
- For AWAITING_RESPONSE: use wait.
- Use the FACTS to personalize — reference what you actually know about them.
- Use the PRODUCT JOURNEY to decide the next conversion step.
- When uncertain, prefer wait over taking action.
- Return ONLY a JSON object with these keys:
  {{"action": "action_name", "reason": "why this action", "params": {{}}}}
"""


def _milestones(memory, prospect_id: str) -> dict:
    """Explicit yes/no flags computed from the FULL event history (not a
    truncated tail). The planner prompt used to dump only the last 5
    events and hope the model noticed a research/qualify event in there -
    once more than 5 other events happened (rate-limit blocks, errors,
    decisions), the marker scrolled out of view and the model would
    re-plan research_prospect / qualify_prospect from scratch, forever.
    These flags are looked up directly against the event store, so they
    are correct regardless of how much has happened since."""
    ev = memory.events
    return {
        "research_done": ev.count_events(prospect_id, "profile_observed") > 0,
        "qualified_ready": ev.last_event_of_type(prospect_id, "qualified") is not None
        and bool((ev.last_event_of_type(prospect_id, "qualified") or {})
                 .get("data", {}).get("qualified")),
        "qualify_attempted": ev.count_events(prospect_id, "qualified") > 0,
        "message_produced": ev.count_events(prospect_id, "message_generated") > 0,
        "message_approved": ev.count_events(prospect_id, "message_approved") > 0,
        "message_sent": ev.count_events(prospect_id, "message_sent") > 0,
        "connection_sent": ev.count_events(prospect_id, "connection_sent") > 0,
    }


def plan_next_action(memory, prospect: dict, campaign: dict,
                     eligibility: list = None,
                     outreach_state: str = None,
                     model: str = DEFAULT_MODEL,
                     base_url: str = DEFAULT_BASE_URL) -> dict:
    """Ask the LLM what to do next for this prospect.

    `memory` is a MemoryService instance. `outreach_state` is the
    campaign-prospect status (MESSAGE_PENDING, AWAITING_RESPONSE, etc.)
    passed directly from the store.

    Returns {"action": str, "reason": str, "params": dict}.
    Falls back to safe defaults if LLM is unreachable OR if the
    milestones make the next step unambiguous - the deterministic path
    is what actually prevents loops; the LLM is only asked to break ties
    the milestones can't resolve on their own (e.g. reply handling).
    """
    prospect_id = prospect["prospect_id"]
    context = memory.get_prospect_context(prospect_id)
    if outreach_state:
        context["outreach_state"] = outreach_state
    context["eligibility"] = eligibility or []
    context["campaign"] = campaign
    context["milestones"] = _milestones(memory, prospect_id)

    # Deterministic fast-path: if the milestones make the next step
    # obvious, skip the LLM entirely. This is the single biggest lever
    # against loops - a 0.8B planner model re-guessing every cycle is
    # the main source of repeated research/qualify/produce calls.
    forced = _fallback_plan(context)
    if forced.get("action") not in ("wait", None):
        return forced

    prompt = _build_planner_prompt(context)

    payload = {
        "model": model,
        "think": False,
        "messages": [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.1},
    }

    try:
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=120)
        if resp.status_code != 200:
            return _fallback_plan(context)
        text = resp.json().get("message", {}).get("content", "")
        plan = _parse_plan(text)
        if plan.get("action"):
            return plan
        return _fallback_plan(context)
    except requests.exceptions.RequestException:
        return _fallback_plan(context)


def _build_planner_prompt(context: dict) -> str:
    state = context.get("state", {})
    facts = context.get("facts", {})
    product = context.get("product_journey", {})
    conv = context.get("conversation_summary", {})
    rel = context.get("relationship", {})
    task = context.get("active_task")
    milestones = context.get("milestones", {})

    return f"""Decide the next action for this prospect.

ALREADY DONE (ground truth from the full history - trust this over RECENT
EVENTS below, which only shows the last few entries):
{json.dumps(milestones, indent=2)}

OUTREACH STATE: {context.get('outreach_state', 'NOT_IN_CAMPAIGN')}

CURRENT STATE:
  Connection: {rel.get('connection_status', 'unknown')}
  Stage: {rel.get('relationship_stage', 'none')}
  Contact count: {rel.get('contact_count', 0)}
  Last contact: {rel.get('last_contact_at', 'never')}

PRODUCT JOURNEY:
  Visited: {product.get('visited', False)}
  Signed up: {product.get('signed_up', False)}
  Activated: {product.get('activated', False)}
  Customer: {product.get('customer', False)}

CONVERSATION:
  Status: {conv.get('status', 'none')}
  Last intent: {conv.get('intent', 'unknown')}
  Objection: {conv.get('objection', 'none')}

FACTS (what we know):
{json.dumps(facts, indent=2) if facts else '  none yet'}

ACTIVE TASK:
{json.dumps(task, indent=2) if task else '  none'}

ELIGIBILITY:
{json.dumps(context.get('eligibility', []), indent=2) if context.get('eligibility') else '  not checked'}

RECENT EVENTS:
{json.dumps(context.get('recent_events', [])[-5:], indent=2) if context.get('recent_events') else '  none'}

CAMPAIGN: {context.get('campaign', {}).get('name', 'unknown')}

IMPORTANT:
- When OUTREACH_STATE is APPROVED_TO_SEND:
  - If there are NO failed send attempts in RECENT EVENTS → action: send_message
  - If there ARE failed send_message events (success: false) → action: retry_send
- When OUTREACH_STATE is MESSAGE_PENDING and no facts/research exist → action: research_prospect
- When OUTREACH_STATE is MESSAGE_PENDING and facts exist but not qualified → action: qualify_prospect
- When OUTREACH_STATE is MESSAGE_PENDING and qualified → action: produce_message
- When OUTREACH_STATE is MESSAGE_REVIEW → action: approve_message

Return ONLY a JSON object:
{{"action": "action_name", "reason": "why", "params": {{}}}}"""


def _parse_plan(text: str) -> dict:
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _fallback_plan(context: dict) -> dict:
    """Safe deterministic fallback when LLM is unreachable.

    Implements the full pipeline flow:
      MESSAGE_PENDING (no facts)  -> research_prospect
      MESSAGE_PENDING (has facts, not qualified) -> qualify_prospect
      MESSAGE_PENDING (has facts, qualified) -> produce_message
      MESSAGE_REVIEW              -> approve_message
      APPROVED_TO_SEND            -> send_message
      AWAITING_RESPONSE           -> wait
      FOLLOWUP_ELIGIBLE           -> follow_up
    """
    outreach = context.get("outreach_state")
    rel = context.get("relationship", {})
    product = context.get("product_journey", {})
    task = context.get("active_task")
    facts = context.get("facts", {})
    recent = context.get("recent_events", [])
    m = context.get("milestones") or {}

    connection = rel.get("connection_status", "unknown")
    signed_up = product.get("signed_up", False)
    activated = product.get("activated", False)
    has_facts = bool(facts)

    # These now come from the full-history milestone flags (event_store
    # lookups), not a scan of the last 5-20 events - so they stay correct
    # no matter how many other events happened in between.
    research_done = m.get("research_done", False)
    qualify_attempted = m.get("qualify_attempted", False)
    qualified_ready = m.get("qualified_ready", False)
    message_produced = m.get("message_produced", False)
    approved = m.get("message_approved", False)

    if task and task.get("next_action"):
        return {"action": task["next_action"]["type"],
                "reason": task["next_action"].get("reason", "task says so"),
                "params": task["next_action"].get("params", {})}

    if outreach == "MESSAGE_PENDING":
        if not has_facts and not research_done:
            return {"action": "research_prospect",
                    "reason": "no facts yet — need research before message",
                    "params": {}}
        if has_facts and not qualify_attempted:
            return {"action": "qualify_prospect",
                    "reason": "facts gathered, need qualification",
                    "params": {}}
        if qualify_attempted and not qualified_ready:
            # Qualification ran and said NOT ready (RESEARCH_MORE or
            # DO_NOT_CONTACT) - producing a message anyway is exactly the
            # "dumb" bug this replaces. Hand it to a human instead of
            # silently messaging an unqualified lead or looping forever.
            return {"action": "handoff",
                    "reason": "qualification did not clear this prospect "
                              "for outreach - needs human review",
                    "params": {}}
        return {"action": "produce_message",
                "reason": "ready to generate message",
                "params": {}}
    if outreach == "MESSAGE_REVIEW":
        already_approved = any(
            m.get("status") == "approved_to_send"
            for m in context.get("messages", []))
        if already_approved:
            return {"action": "approve_message",
                    "reason": "message already approved, transition state",
                    "params": {}}
        return {"action": "approve_message",
                "reason": "auto-approve generated message", "params": {}}
    if outreach == "APPROVED_TO_SEND":
        send_failed = any(
            e.get("data", {}).get("action") == "send_message"
            and not e.get("data", {}).get("success", True)
            for e in recent)
        if send_failed:
            return {"action": "retry_send",
                    "reason": "previous send failed, retrying with fresh attempt",
                    "params": {}}
        return {"action": "send_message",
                "reason": "message approved, ready to send", "params": {}}
    if outreach == "FOLLOWUP_ELIGIBLE":
        return {"action": "follow_up",
                "reason": "follow-up is due", "params": {}}
    if outreach == "AWAITING_RESPONSE":
        return {"action": "wait",
                "reason": "waiting for prospect response", "params": {}}

    if connection in ("unknown", "not_connected"):
        return {"action": "send_connection_request",
                "reason": "not yet connected", "params": {}}
    if connection == "pending":
        return {"action": "wait",
                "reason": "waiting for connection acceptance", "params": {}}
    if signed_up and not activated:
        return {"action": "follow_up",
                "reason": "signed up but not activated", "params": {}}
    return {"action": "wait",
            "reason": "no clear next step", "params": {}}
