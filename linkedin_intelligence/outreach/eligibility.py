"""Deterministic outreach eligibility.

Eligibility is a pure rule function - the LLM NEVER decides who is eligible.
Every check is expressed as (ok: bool, reason: str) so a GUI can show exactly
why a prospect passed or failed.

Formula (from the spec):
  human_approved AND not do_not_contact AND not already_customer
  AND not recently_contacted AND no active conversation
  AND campaign_segment_match AND evidence_confidence >= threshold
"""

from datetime import datetime, timedelta, timezone

from ..utils import campaign_config

# Campaign-prospect states that mean "this conversation is still live".
ACTIVE_STATES = {
    "CAMPAIGN_ASSIGNED", "MESSAGE_PENDING", "MESSAGE_GENERATED",
    "MESSAGE_REVIEW", "APPROVED_TO_SEND", "SENT", "AWAITING_RESPONSE",
    "RESPONDED", "CONVERSATION", "INTERESTED", "HIGH_INTENT",
    "FOLLOWUP_ELIGIBLE", "LINK_SHARED", "VISITED", "SIGNUP_STARTED",
    "SIGNED_UP", "ACTIVATED",
}

APPROVED_STATUSES = {"ready_for_outreach", "approved", "human_approved", "new"}
BLOCKED_STATUSES = {"do_not_contact", "not_relevant", "already_customer",
                    "declined", "customer", "rejected", "unqualified"}


def _utcnow() -> datetime:
    from ..timeutil import now as _now_local
    return _now_local()


def _eval(checks: list) -> tuple:
    return all(ok for ok, _ in checks), checks


def is_eligible(store, prospect: dict, campaign: dict,
                now: datetime = None) -> tuple:
    """Return (eligible: bool, checks: [(ok, reason)]). `campaign` is the
    campaign config dict (from config/campaigns/*.yaml)."""
    now = now or _utcnow()
    checks = []
    cfg = campaign_config(campaign)

    # 1. Human approval - a prospect must be explicitly approved by a human.
    approved = False
    for f in store.feedback(prospect.get("prospect_id")):
        if f.get("action") in ("ready_for_outreach", "approved"):
            approved = True
    if not approved and prospect.get("status") in APPROVED_STATUSES:
        approved = True
    checks.append((approved, "human_approved"))

    # 2. Blocked statuses.
    blocked = prospect.get("status") in BLOCKED_STATUSES
    checks.append((not blocked, "not_blocked"))

    # 3. Already a customer / do-not-contact exclusion table.
    excl_block = False
    for e in store.exclusions(prospect.get("prospect_id")):
        for r in (e.get("reasons") or []):
            if "do not contact" in str(r).lower() or "customer" in str(r).lower():
                excl_block = True
    checks.append((not excl_block, "no_exclusion"))

    # 4. Not recently contacted (cooldown from campaign limits).
    limits = cfg.get("limits", {})
    cooldown_days = int(limits.get("contact_cooldown_days", 60))
    last = prospect.get("last_contacted")
    recently = False
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            recently = (now - last_dt) < timedelta(days=cooldown_days)
        except ValueError:
            recently = False
    checks.append((not recently, "not_recently_contacted"))

    # 5. No active conversation in ANY campaign.
    active = False
    for cp in store.campaign_prospects(prospect_id=prospect.get("prospect_id")):
        if cp.get("status") in ACTIVE_STATES:
            active = True
            break
    checks.append((not active, "no_active_conversation"))

    # 6. Campaign segment match. A campaign targets one or more segments; the
    #    prospect must belong to at least one, or be in the campaign's target
    #    segment list directly.
    target_segments = set()
    for seg in cfg.get("target", {}).get("segments", []):
        target_segments.add(str(seg).lower())
    prospect_segments = {str(s).lower() for s in prospect.get("segments", [])}
    seg_match = not target_segments or bool(prospect_segments & target_segments)
    checks.append((seg_match, "campaign_segment_match"))

    # 7. Evidence confidence >= campaign threshold.
    threshold = float(cfg.get("limits", {}).get("min_evidence_confidence", 0.5))
    conf = prospect.get("qualification_confidence")
    if conf is None:
        conf = 0.0
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0.0
    checks.append((conf >= threshold, "evidence_confidence_threshold"))

    return all(ok for ok, _ in checks), checks


def is_blocked(store, prospect: dict) -> bool:
    """True when a prospect must never receive outreach: blocked status or an
    exclusion record. Used by the message validator (an ASSIGNED prospect is
    in an active conversation by design, so the full eligibility rule - which
    forbids that - does not apply to messages)."""
    if prospect.get("status") in BLOCKED_STATUSES:
        return True
    for e in store.exclusions(prospect.get("prospect_id")):
        for r in (e.get("reasons") or []):
            if "do not contact" in str(r).lower() or "customer" in str(r).lower():
                return True
    return False


def eligible_prospects(store, campaign: dict, prospects: list) -> list:
    """Filter `prospects` to those passing every eligibility check."""
    out = []
    for p in prospects:
        ok, checks = is_eligible(store, p, campaign)
        if ok:
            p["eligibility_checks"] = checks
            out.append(p)
    return out
