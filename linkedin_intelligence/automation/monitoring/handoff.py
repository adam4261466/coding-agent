"""Human handoff + real, time-based rate limiting.

THE BUG THIS REPLACES: the previous check_rate_limit() called
`event_store.last_event_of_type(prospect_id, action_type)` - a lookup by
`event_type`. But nothing in this codebase ever writes an event whose
event_type equals an action name like "send_message" or
"send_connection_request": the code writes outcome-labelled events
("message_sent", "connection_sent", "profile_observed"...) or the generic
"action_executed". Because the string never matched, the lookup always
returned None, `allowed` was always True, and every call site
(produce_message, send_message, follow_up, research_prospect, and every
ACTION_MAP browser action) was rate-limited in name only. There is
currently no functioning brake on how fast this agent connects, messages,
or views profiles on a real LinkedIn account.

THE FIX: rate limiting now reads the dedicated `action_log` table (see
automation/memory/event_store.py), which is keyed by the actual action
name and indexed for exactly this query. Two independent guards:

  1. Per-prospect cooldown - don't repeat the same action on the same
     person before COOLDOWNS[action] has elapsed.
  2. Account-wide daily cap - regardless of how many prospects are in the
     pipeline, never exceed DAILY_CAPS[action] executions of that action
     in a rolling 24h window. This is what actually protects the LinkedIn
     account; the per-prospect cooldown alone doesn't, since 500
     prospects each cooling down independently is still 500 actions/day.

A failed attempt (success=False) is excluded from the cooldown check, so
a genuine failure can be retried without waiting out the full window -
but it still counts toward the daily cap, so a broken retry loop can't
hammer LinkedIn under the guise of "just retrying".

COOLDOWNS / DAILY_CAPS below are conservative starting points, not a
guarantee of safety - tune them to your account's age and history.
"""

from datetime import datetime, timedelta, timezone

from ..memory.event_store import EventStore

COOLDOWNS = {
    "send_connection_request": timedelta(hours=24),
    "send_message": timedelta(hours=12),
    "follow_up": timedelta(hours=12),
    "research_prospect": timedelta(hours=6),
    "produce_message": timedelta(hours=6),
    "observe_profile": timedelta(hours=6),
    "observe_conversation": timedelta(hours=1),
    "view_profile": timedelta(hours=1),
    "endorse_skill": timedelta(days=7),
}
DEFAULT_COOLDOWN = timedelta(hours=1)

# None here (or a missing key) means "no account-wide cap for this action".
DAILY_CAPS = {
    "send_connection_request": 20,
    "send_message": 30,
    "follow_up": 30,
    "view_profile": 100,
    "observe_profile": 60,
    "endorse_skill": 15,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def handoff_needed(event_store: EventStore, reason: str, prospect_id: str,
                   campaign_id: str = None, urgency: str = "normal",
                   context: str = None):
    """Request human intervention. Logs the handoff event."""
    event_store.append(
        "handoff_requested", prospect_id, campaign_id,
        data={"reason": reason, "urgency": urgency, "context": context})
    return True


def check_rate_limit(event_store: EventStore, action_type: str,
                     prospect_id: str, now: datetime = None) -> bool:
    """True if `action_type` is allowed right now for `prospect_id`.

    Checks the per-prospect cooldown first, then the account-wide daily
    cap. Logs a handoff event whenever it blocks something, saying
    exactly which guard fired and how long until it clears - so the
    reason is visible in the event log, not just a silent skip.
    """
    now = now or _utcnow()

    cooldown = COOLDOWNS.get(action_type, DEFAULT_COOLDOWN)
    last = event_store.last_action_time(action_type, prospect_id, success_only=True)
    if last is not None and (now - last) < cooldown:
        wait = cooldown - (now - last)
        handoff_needed(
            event_store, reason=f"rate_limit:{action_type}",
            prospect_id=prospect_id, urgency="low",
            context=f"Last successful {action_type} was at {last.isoformat()}; "
                    f"{wait} remaining on the {cooldown} cooldown.")
        return False

    cap = DAILY_CAPS.get(action_type)
    if cap is not None:
        since = now - timedelta(hours=24)
        count = event_store.action_count_since(action_type, since)
        if count >= cap:
            handoff_needed(
                event_store, reason=f"daily_cap:{action_type}",
                prospect_id=prospect_id, urgency="normal",
                context=f"{count}/{cap} {action_type} actions in the last 24h - "
                        f"pausing this action type until the window rolls over.")
            return False

    return True


def check_approval_required(campaign: dict) -> bool:
    """True if campaign requires human approval (always True in v1)."""
    approval = campaign.get("approval", {})
    return approval.get("required", True)
