"""Next-action assignment: every prospect ends research in exactly one state.

Hard rules (declined / do-not-contact) win over model suggestions. A
READY_FOR_HUMAN_REVIEW recommendation also requires enough confidence, so a
low-confidence model guess does not enter the human approval queue.
"""


def assign_next_action(prospect: dict, qualification: dict,
                       min_confidence_for_ready: float = 0.5) -> str:
    signal = prospect.get("commercial_intent", "unknown")
    action = str(qualification.get("recommended_next_action", "RESEARCH_MORE")).upper()
    confidence = qualification.get("confidence") or 0.0

    # Hard rules win over anything the model says.
    if signal == "declined" or prospect.get("status") in (
            "do_not_contact", "not_relevant", "already_customer", "declined"):
        return "DO_NOT_CONTACT"
    if prospect.get("status") == "ready_for_outreach":
        return "READY_FOR_OUTREACH"

    if action not in ("SKIP", "RESEARCH_MORE", "READY_FOR_HUMAN_REVIEW",
                      "READY_FOR_OUTREACH", "FOLLOW_UP", "DO_NOT_CONTACT"):
        action = "RESEARCH_MORE"

    # Confidence gate: only well-supported suggestions reach the human queue.
    if action in ("READY_FOR_HUMAN_REVIEW", "READY_FOR_OUTREACH") \
            and confidence < min_confidence_for_ready:
        action = "RESEARCH_MORE"

    # A recent outbound conversation that needs a reply is a follow-up.
    if prospect.get("conversation_count", 0) > 0 and prospect.get("last_contacted"):
        if action == "RESEARCH_MORE" and signal not in ("none", "unknown", "declined"):
            return "FOLLOW_UP"
    return action


def apply(store, prospect: dict, qualification: dict,
          min_confidence_for_ready: float = 0.5):
    action = assign_next_action(prospect, qualification, min_confidence_for_ready)
    status_map = {
        "SKIP": "skipped",
        "RESEARCH_MORE": "research",
        "READY_FOR_HUMAN_REVIEW": "ready_for_human_review",
        "READY_FOR_OUTREACH": "ready_for_outreach",
        "FOLLOW_UP": "follow_up",
        "DO_NOT_CONTACT": "do_not_contact",
    }
    status = status_map.get(action, "research")
    store.set_status(prospect["prospect_id"], status, next_action=action)
    return action
