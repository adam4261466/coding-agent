"""Phase 4 calibration: how well do Phase 2 predictions translate to real
outreach outcomes? This closes the loop - the model's "READY_FOR_OUTREACH"
approvals are compared with actual replies / activations for the prospects
that were contacted.

Numbers only; interpretation is for the human (or the LLM, never inventing).
"""


def _approved_prospects(store) -> dict:
    """prospect_id -> True for humans who were approved for outreach."""
    approved = set()
    for f in store.feedback():
        if f.get("action") in ("ready_for_outreach", "approved"):
            approved.add(f.get("prospect_id"))
    for p in store.prospects(status="ready_for_outreach"):
        approved.add(p["prospect_id"])
    return approved


def _contacted(store) -> dict:
    """prospect_id -> True for those actually sent a message."""
    from ..outreach.attribution import ACTIVE_STATES
    contacted = set()
    for cp in store.campaign_prospects():
        if cp.get("status") in ACTIVE_STATES:
            contacted.add(cp["prospect_id"])
    return contacted


def _outcome(store, prospect_id: str) -> str:
    """Best observed outcome for a contacted prospect."""
    convs = store.conversations_outreach(prospect_id)
    if any(c.get("intent") == "interest" or
           c.get("commercial_intent") == "high" for c in convs):
        return "positive_reply"
    if convs:
        return "replied"
    for ev in ("customer", "activated", "signed_up"):
        if store.product_events(prospect_id=prospect_id, event_name=ev):
            return ev
    return "no_reply"


def build_optimization_calibration(store) -> dict:
    approved = _approved_prospects(store)
    contacted = _contacted(store)

    eligible_contacted = contacted
    outcomes = {pid: _outcome(store, pid) for pid in eligible_contacted}

    approved_and_contacted = approved & set(outcomes)
    positive = {pid for pid, o in outcomes.items() if o in
                ("positive_reply", "activated", "signed_up", "customer")}

    tp = len(approved_and_contacted & positive)
    fp = len(approved_and_contacted - positive)
    contacted_total = len(eligible_contacted)

    return {
        "human_approved": len(approved),
        "contacted": contacted_total,
        "contacted_and_approved": len(approved_and_contacted),
        "positive_outcomes": len(positive),
        "true_positives": tp,
        "false_positives": fp,
        "precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
        "activation_rate_of_approved": round(
            len([p for p in approved_and_contacted
                 if _outcome(store, p) in ("activated", "signed_up", "customer")]) /
            len(approved_and_contacted), 3) if approved_and_contacted else None,
        "outcomes": outcomes,
    }
