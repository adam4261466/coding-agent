"""Funnel analysis: contact -> visit -> signup -> activation -> customer, with
the largest drop-off flagged as the bottleneck. Pure counts + statistics."""

from ..outreach.attribution import funnel_for_campaign
from .evaluator import wilson_ci


def funnel_analysis(store, campaign_id: str = None) -> dict:
    if campaign_id:
        funnels = [funnel_for_campaign(store, campaign_id)]
    else:
        funnels = [funnel_for_campaign(store, c["campaign_id"])
                   for c in store.campaigns()]
    totals = {"contacted": 0, "visited": 0, "signup_started": 0,
              "signed_up": 0, "activated": 0, "customer": 0}
    for f in funnels:
        for k in totals:
            totals[k] += f[k]

    stages = [
        ("contacted", "visited"),
        ("visited", "signup_started"),
        ("signup_started", "signed_up"),
        ("signed_up", "activated"),
        ("activated", "customer"),
    ]
    steps = []
    for src, dst in stages:
        n_from = totals[src]
        n_to = totals[dst]
        rate = wilson_ci(n_to, n_from)
        steps.append({
            "from": src, "to": dst,
            "n_from": n_from, "n_to": n_to,
            "rate": rate["rate"], "ci_low": rate["ci_low"],
            "ci_high": rate["ci_high"],
        })

    # Bottleneck = the step with the largest proportional drop-off, ignoring
    # steps with zero entrants.
    bottleneck = None
    for s in steps:
        if s["n_from"] <= 0:
            continue
        drop = 1.0 - s["rate"]
        if bottleneck is None or drop > bottleneck["drop"]:
            bottleneck = {**s, "drop": drop}

    return {
        "totals": totals,
        "steps": steps,
        "bottleneck": bottleneck,
    }


def biggest_gap_between_segments(store) -> dict:
    """Largest activation-rate gap between two segments with sufficient
    samples. Never auto-acts on this; it feeds recommendations."""
    from .segment_performance import segment_performance
    segs = [r for r in segment_performance(store) if r["activation_rate"]["sufficient"]]
    if len(segs) < 2:
        return None
    segs.sort(key=lambda r: r["activation_rate"]["rate"])
    low, high = segs[0], segs[-1]
    return {
        "low": low["segment"], "high": high["segment"],
        "low_rate": low["activation_rate"]["rate"],
        "high_rate": high["activation_rate"]["rate"],
        "gap": round(high["activation_rate"]["rate"] -
                     low["activation_rate"]["rate"], 4),
    }
