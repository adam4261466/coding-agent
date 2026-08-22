"""Outbound sequence scheduling: when a follow-up becomes eligible.

Fully deterministic. The sequence reads `sequence.follow_up_days` from the
campaign config; a prospect becomes FOLLOWUP_ELIGIBLE only after the last
message sat unanswered for the configured number of days, and only if the
follow-up budget (max_follow_ups) has not been exhausted.
"""

from datetime import datetime, timedelta, timezone

from ..utils import campaign_config
from .state_machine import transition


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def follow_up_config(campaign: dict) -> dict:
    seq = campaign_config(campaign).get("sequence", {})
    days = seq.get("follow_up_days") or [3, 7, 14]
    if isinstance(days, int):
        days = [days]
    return {
        "days": sorted(int(d) for d in days),
        "max_follow_ups": int(seq.get("max_follow_ups", 2)),
    }


def sent_follow_up_count(store, prospect_id: str, campaign_id: str) -> int:
    """Count follow-ups already SENT (past the first message) for this
    prospect in this campaign, derived from the event log."""
    events = store.outreach_events(prospect_id, campaign_id)
    count = 0
    for e in events:
        if e.get("event") in ("follow_up_sent", "sent") and \
                e.get("from_state") in ("MESSAGE_REVIEW", "APPROVED_TO_SEND",
                                        "FOLLOWUP_ELIGIBLE", "MESSAGE_PENDING"):
            count += 1
    return max(0, count - 1)


def _last_outbound_time(store, prospect_id: str, campaign_id: str):
    events = [e for e in store.outreach_events(prospect_id, campaign_id)
              if e.get("event") in ("sent", "follow_up_sent")]
    if not events:
        return None
    events.sort(key=lambda e: e.get("created_at") or "")
    last = events[-1].get("created_at")
    try:
        dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def mark_sent(store, campaign_prospect: dict) -> dict:
    """Record that a message went out (human clicked 'mark sent'). Moves the
    prospect to SENT/AWAITING_RESPONSE."""
    try:
        cp = transition(store, campaign_prospect, "SENT",
                        event="sent", note="first outbound message sent")
        return transition(store, cp, "AWAITING_RESPONSE",
                          event="sent", note="message sent, awaiting reply")
    except ValueError:
        try:
            return transition(store, campaign_prospect, "AWAITING_RESPONSE",
                              event="follow_up_sent", note="follow-up sent")
        except ValueError:
            return campaign_prospect


def due_follow_ups(store, campaign: dict, now: datetime = None) -> list:
    """All AWAITING_RESPONSE prospects whose follow-up is due today and whose
    follow-up budget remains. Returns campaign_prospect dicts."""
    now = now or _utcnow()
    cfg = follow_up_config(campaign)
    out = []
    for cp in store.campaign_prospects(campaign["campaign_id"],
                                       status="AWAITING_RESPONSE"):
        last = _last_outbound_time(store, cp["prospect_id"], campaign["campaign_id"])
        if not last:
            continue
        sent = sent_follow_up_count(store, cp["prospect_id"], campaign["campaign_id"])
        if sent >= cfg["max_follow_ups"]:
            continue
        idx = min(sent, len(cfg["days"]) - 1)
        due_at = last + timedelta(days=cfg["days"][idx])
        if now >= due_at:
            out.append(cp)
    return out


def make_follow_up_eligible(store, campaign: dict, now: datetime = None) -> list:
    """Move due AWAITING_RESPONSE prospects to FOLLOWUP_ELIGIBLE. Idempotent."""
    moved = []
    for cp in due_follow_ups(store, campaign, now):
        try:
            moved.append(transition(store, cp, "FOLLOWUP_ELIGIBLE",
                                    event="follow_up_due"))
        except ValueError:
            continue
    return moved
