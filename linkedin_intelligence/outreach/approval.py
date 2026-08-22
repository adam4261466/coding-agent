"""Approval queue. A message only becomes sendable after an explicit human
approval. The queue is ordered by priority and carries the full validation
verdict with every candidate.
"""

from .state_machine import transition


def approval_queue(store, campaign_id: str = None, limit: int = None) -> list:
    """Messages that passed validation and await a human decision. Ordered by
    campaign priority then recency."""
    msgs = store.messages_for(status="approved")
    rows = []
    for m in msgs:
        cp = store.get_campaign_prospect(m.get("campaign_id"), m.get("prospect_id"))
        if not cp:
            continue
        if campaign_id and m.get("campaign_id") != campaign_id:
            continue
        rows.append((m, cp))
    rows.sort(key=lambda x: (x[1].get("priority") or 0), reverse=True)
    out = [m for m, _ in rows]
    return out[:limit] if limit else out


def approve(store, message_id: str) -> dict:
    """Human approves a message: mark approved, move campaign-prospect to
    APPROVED_TO_SEND."""
    msg = store.get_message(message_id)
    if not msg:
        raise ValueError(f"no message {message_id}")
    if msg.get("status") == "rejected":
        raise ValueError("cannot approve a rejected message")
    store.set_message_status(message_id, "approved_to_send", approved=True)
    cp = store.get_campaign_prospect(msg["campaign_id"], msg["prospect_id"])
    if cp:
        transition(store, cp, "APPROVED_TO_SEND", event="human_approved",
                   note=f"message {message_id}")
    msg["approved"] = True
    msg["status"] = "approved_to_send"
    return msg


def reject(store, message_id: str, note: str = None) -> dict:
    """Human rejects a message: back to MESSAGE_REVIEW (regeneration path) or
    if it failed validation, to needs_rework."""
    msg = store.get_message(message_id)
    if not msg:
        raise ValueError(f"no message {message_id}")
    store.set_message_status(message_id, "rejected", approved=False)
    store.log_outreach_event(
        prospect_id=msg["prospect_id"], campaign_id=msg["campaign_id"],
        from_state="MESSAGE_REVIEW", to_state="MESSAGE_GENERATED",
        event="rejected", note=note or f"message {message_id} rejected by human")
    msg["status"] = "rejected"
    msg["approved"] = False
    return msg


def queue_counts(store, campaign_id: str = None) -> dict:
    msgs = approval_queue(store, campaign_id)
    return {
        "pending": len(msgs),
        "approved": len([m for m in store.messages_for(status="approved_to_send")]),
        "rejected": len([m for m in store.messages_for(status="rejected")]),
    }
