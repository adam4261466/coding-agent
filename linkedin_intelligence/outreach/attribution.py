"""Attribution: campaign -> message(version) -> visit -> signup -> activation
-> customer. Every product event is linked back to the prospect and, when
available, the exact message version that started the chain.

This is first-touch attribution at the prospect level: the campaign that
contacted the prospect gets the credit for their website events.
"""

ACTIVE_STATES = {
    "SENT", "AWAITING_RESPONSE", "RESPONDED", "CONVERSATION", "INTERESTED",
    "HIGH_INTENT", "NOT_INTERESTED", "FOLLOWUP_ELIGIBLE", "LINK_SHARED",
    "VISITED", "SIGNUP_STARTED", "SIGNED_UP", "ACTIVATED", "CUSTOMER",
}


def attribution_for(store, prospect_id: str) -> dict:
    """The full attribution chain for one prospect."""
    events = store.product_events(prospect_id=prospect_id)
    events.sort(key=lambda e: e.get("created_at") or "")
    messages = store.messages_for(prospect_id=prospect_id)
    return {
        "prospect_id": prospect_id,
        "campaigns": sorted({m.get("campaign_id") for m in messages}),
        "messages": [
            {"message_id": m["message_id"], "campaign_id": m.get("campaign_id"),
             "version": m.get("version"), "strategy": m.get("strategy"),
             "approved": m.get("approved"), "status": m.get("status"),
             "created_at": m.get("created_at")}
            for m in messages
        ],
        "product_events": events,
    }


def first_event(store, prospect_id: str, event_name: str) -> dict:
    evs = [e for e in store.product_events(prospect_id=prospect_id)
           if e.get("event_name") == event_name]
    if not evs:
        return None
    evs.sort(key=lambda e: e.get("created_at") or "")
    return evs[0]


def funnel_for_campaign(store, campaign_id: str) -> dict:
    """Counts per campaign: how many contacted prospects reached each stage."""
    cps = store.campaign_prospects(campaign_id)
    contacted = [cp for cp in cps if cp.get("status") in ACTIVE_STATES]
    visited = set()
    signup_started = set()
    signed_up = set()
    activated = set()
    customer = set()
    for e in store.product_events():
        if e.get("campaign_id") != campaign_id:
            continue
        pid = e.get("prospect_id")
        name = e.get("event_name")
        if name == "visit":
            visited.add(pid)
        elif name == "signup_started":
            signup_started.add(pid)
        elif name == "signed_up":
            signed_up.add(pid)
        elif name == "activated":
            activated.add(pid)
        elif name == "customer":
            customer.add(pid)
    base = len(contacted)
    return {
        "campaign_id": campaign_id,
        "contacted": base,
        "visited": len(visited),
        "signup_started": len(signup_started),
        "signed_up": len(signed_up),
        "activated": len(activated),
        "customer": len(customer),
        "visit_rate": _pct(len(visited), base),
        "signup_rate": _pct(len(signed_up), base),
        "activation_rate": _pct(len(activated), base),
        "customer_rate": _pct(len(customer), base),
    }


def _pct(n, d) -> float:
    return round(100.0 * n / d, 1) if d else 0.0
