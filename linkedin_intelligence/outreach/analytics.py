"""Phase 3 analytics: rates per segment and per strategy, from real event and
state data only. The LLM never computes these numbers - it may only interpret
them (Phase 4).

Rates are always measured against CONTACTED prospects (a message was
approved and sent), so a campaign that never sent anything shows 0%, not a
division error.
"""

from .attribution import ACTIVE_STATES, first_event
from .eligibility import APPROVED_STATUSES

REPLY_INTENTS = {"question", "interest", "objection", "follow_up", "off_topic",
                 "not_interested"}
POSITIVE_INTENTS = {"question", "interest", "follow_up"}


def _sent_state(status: str) -> bool:
    return status in ACTIVE_STATES


def _replied_state(status: str) -> bool:
    return status in {"RESPONDED", "CONVERSATION", "INTERESTED", "HIGH_INTENT",
                      "NOT_INTERESTED", "FOLLOWUP_ELIGIBLE", "LINK_SHARED",
                      "VISITED", "SIGNUP_STARTED", "SIGNED_UP", "ACTIVATED",
                      "CUSTOMER"}


def _conversations(store, prospect_id: str) -> list:
    return store.conversations_outreach(prospect_id)


def metrics_for(store, cps: list, campaign_id: str = None) -> dict:
    """Compute funnel + reply metrics for a set of campaign-prospect rows."""
    contacted = [cp for cp in cps if _sent_state(cp.get("status"))]
    base = len(contacted)
    replied = [cp for cp in contacted if _replied_state(cp.get("status"))]
    positive = []
    visited = set()
    signup_started = set()
    signed_up = set()
    activated = set()
    customer = set()

    replied_ids = {cp["prospect_id"] for cp in replied}
    for cp in contacted:
        pid = cp["prospect_id"]
        convs = _conversations(store, pid)
        if any(c.get("intent") in REPLY_INTENTS for c in convs):
            replied_ids.add(pid)
        if any(c.get("intent") in POSITIVE_INTENTS for c in convs):
            positive.append(cp)
        for ev_name, sink in (("visit", visited), ("signup_started", signup_started),
                              ("signed_up", signed_up), ("activated", activated),
                              ("customer", customer)):
            if first_event(store, pid, ev_name):
                sink.add(pid)

    return {
        "contacted": base,
        "replied": len(replied_ids),
        "positive_replies": len(positive),
        "visited": len(visited),
        "signup_started": len(signup_started),
        "signed_up": len(signed_up),
        "activated": len(activated),
        "customer": len(customer),
        "reply_rate": _pct(len(replied), base),
        "positive_reply_rate": _pct(len(positive), base),
        "visit_rate": _pct(len(visited), base),
        "signup_rate": _pct(len(signed_up), base),
        "activation_rate": _pct(len(activated), base),
        "customer_rate": _pct(len(customer), base),
    }


def per_strategy(store, campaign_id: str = None) -> dict:
    """Metrics broken down by assigned strategy."""
    cps = store.campaign_prospects(campaign_id)
    groups = {}
    for cp in cps:
        key = cp.get("assigned_strategy") or "unknown"
        groups.setdefault(key, []).append(cp)
    return {k: metrics_for(store, v) for k, v in sorted(groups.items())}


def per_segment(store, campaign_id: str = None) -> dict:
    """Metrics broken down by the prospect's ICP segment(s)."""
    by_seg = {}
    for cp in store.campaign_prospects(campaign_id):
        p = store.get_prospect(cp["prospect_id"])
        if not p:
            continue
        segs = p.get("segments") or ["unsegmented"]
        for seg in segs:
            by_seg.setdefault(seg, []).append(cp)
    return {k: metrics_for(store, v) for k, v in sorted(by_seg.items())}


def overview(store, campaign_id: str = None) -> dict:
    cps = store.campaign_prospects(campaign_id)
    m = metrics_for(store, cps, campaign_id)
    m["campaign_id"] = campaign_id
    m["by_strategy"] = per_strategy(store) if not campaign_id else {}
    m["by_segment"] = per_segment(store) if not campaign_id else {}
    return m


def _pct(n, d) -> float:
    return round(100.0 * n / d, 1) if d else 0.0
