"""Explicit outreach state machine.

Every prospect-in-campaign lives in exactly one state. Transitions are
validated deterministically - the LLM never moves a prospect between states.
`transition()` rejects illegal moves and writes an audit row to
outreach_events so the entire funnel history is reconstructable.
"""

ALL_STATES = {
    "DISCOVERED", "QUALIFIED", "HUMAN_APPROVED", "CAMPAIGN_ASSIGNED",
    "MESSAGE_PENDING", "MESSAGE_GENERATED", "MESSAGE_REVIEW",
    "APPROVED_TO_SEND", "SENT", "AWAITING_RESPONSE", "RESPONDED",
    "CONVERSATION", "INTERESTED", "HIGH_INTENT", "NOT_INTERESTED",
    "FOLLOWUP_ELIGIBLE", "LINK_SHARED", "VISITED", "SIGNUP_STARTED",
    "SIGNED_UP", "ACTIVATED", "CUSTOMER",
    "DO_NOT_CONTACT", "REJECTED", "UNQUALIFIED",
}

TERMINAL = {"DO_NOT_CONTACT", "REJECTED", "UNQUALIFIED", "CUSTOMER"}

# Explicit legal moves. DO_NOT_CONTACT is additionally allowed from any
# non-terminal state (a human or the prospect can opt out at any time).
TRANSITIONS = {
    "DISCOVERED": {"QUALIFIED"},
    "QUALIFIED": {"HUMAN_APPROVED", "REJECTED", "UNQUALIFIED"},
    "HUMAN_APPROVED": {"CAMPAIGN_ASSIGNED", "REJECTED"},
    "CAMPAIGN_ASSIGNED": {"MESSAGE_PENDING", "REJECTED"},
    "MESSAGE_PENDING": {"MESSAGE_GENERATED"},
    "MESSAGE_GENERATED": {"MESSAGE_REVIEW", "MESSAGE_PENDING"},
    "MESSAGE_REVIEW": {"APPROVED_TO_SEND", "MESSAGE_GENERATED", "REJECTED"},
    "APPROVED_TO_SEND": {"SENT"},
    "SENT": {"AWAITING_RESPONSE", "RESPONDED"},
    "AWAITING_RESPONSE": {"RESPONDED", "FOLLOWUP_ELIGIBLE"},
    "FOLLOWUP_ELIGIBLE": {"MESSAGE_PENDING"},
    "RESPONDED": {"CONVERSATION"},
    "CONVERSATION": {"INTERESTED", "HIGH_INTENT", "NOT_INTERESTED"},
    "INTERESTED": {"LINK_SHARED", "HIGH_INTENT", "NOT_INTERESTED"},
    "HIGH_INTENT": {"LINK_SHARED", "INTERESTED"},
    "NOT_INTERESTED": set(),
    "LINK_SHARED": {"VISITED", "SIGNUP_STARTED", "INTERESTED"},
    "VISITED": {"SIGNUP_STARTED"},
    "SIGNUP_STARTED": {"SIGNED_UP"},
    "SIGNED_UP": {"ACTIVATED"},
    "ACTIVATED": {"CUSTOMER"},
}


def validate_state(state: str) -> str:
    state = str(state or "CAMPAIGN_ASSIGNED").upper()
    if state not in ALL_STATES:
        raise ValueError(f"unknown outreach state: {state}")
    return state


def can_transition(from_state: str, to_state: str) -> bool:
    from_state = validate_state(from_state)
    to_state = validate_state(to_state)
    if from_state in TERMINAL:
        return False
    if from_state == to_state:
        return True  # no-op
    if to_state == "DO_NOT_CONTACT" and from_state != "NOT_INTERESTED":
        return True  # opt-out allowed from any active state
    return to_state in TRANSITIONS.get(from_state, set())


def transition(store, campaign_prospect: dict, to_state: str,
               event: str = None, note: str = None) -> dict:
    """Move one campaign-prospect to `to_state`. Deterministic, audit-logged.
    Moving to the current state is a no-op (no audit row)."""
    cp = dict(campaign_prospect)
    from_state = validate_state(cp.get("status", "CAMPAIGN_ASSIGNED"))
    to_state = validate_state(to_state)
    if not can_transition(from_state, to_state):
        raise ValueError(f"illegal transition {from_state} -> {to_state}")
    if from_state == to_state:
        cp["status"] = to_state
        return cp
    store.log_outreach_event(
        prospect_id=cp["prospect_id"], campaign_id=cp["campaign_id"],
        from_state=from_state, to_state=to_state,
        event=event or f"{from_state}->{to_state}", note=note)
    store.set_campaign_prospect_status(cp["campaign_id"], cp["prospect_id"],
                                       to_state)
    cp["status"] = to_state
    return cp
