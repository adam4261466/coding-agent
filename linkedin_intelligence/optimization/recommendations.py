"""Recommendations + ICP learning report.

Every recommendation carries the data that motivated it and REQUIRES human
approval before anything is applied. Nothing in Phase 4 changes campaigns,
messaging, or the ICP automatically.
"""

from ..utils import load_icp
from .evaluator import two_proportion_test
from .funnel_analysis import funnel_analysis, biggest_gap_between_segments
from .segment_performance import segment_performance
from .strategy_performance import strategy_performance
from .message_performance import repeated_phrases


def recommendations(store) -> list:
    """Build an ordered recommendation list from observed data only."""
    out = []
    funnel = funnel_analysis(store)

    bn = funnel.get("bottleneck")
    if bn and bn["n_from"] >= 10:
        out.append({
            "priority": 1,
            "area": "funnel",
            "action": f"Investigate the {bn['from']}->{bn['to']} step "
                      f"({bn['rate']:.0%}); it is the largest drop-off.",
            "requires_approval": True,
            "evidence": f"{bn['n_to']}/{bn['n_from']} converted",
        })

    gap = biggest_gap_between_segments(store)
    if gap and gap["gap"] >= 0.1:
        out.append({
            "priority": 2,
            "area": "segment",
            "action": (f"Run a controlled experiment comparing segment "
                       f"'{gap['low']}' and '{gap['high']}' before any "
                       f"targeting change."),
            "requires_approval": True,
            "evidence": f"activation {gap['low_rate']:.0%} vs {gap['high_rate']:.0%}",
        })

    strategies = strategy_performance(store)
    suff = [s for s in strategies if s["activation_rate"]["sufficient"]]
    if len(suff) >= 2:
        s0 = max(suff, key=lambda s: s["activation_rate"]["rate"])
        s1 = min(suff, key=lambda s: s["activation_rate"]["rate"])
        if s0["activation_rate"]["rate"] - s1["activation_rate"]["rate"] >= 0.05:
            out.append({
                "priority": 3,
                "area": "strategy",
                "action": (f"Consider favoring '{s0['strategy']}' over "
                           f"'{s1['strategy']}' via an experiment, not a "
                           f"hard switch."),
                "requires_approval": True,
                "evidence": f"activation {s0['activation_rate']['rate']:.0%} "
                            f"vs {s1['activation_rate']['rate']:.0%}",
            })

    for p in repeated_phrases(store):
        if p["count"] >= 3:
            out.append({
                "priority": 4,
                "area": "messaging",
                "action": (f"Message opener '{p['opener']}' was used "
                           f"{p['count']}x; consider varying it."),
                "requires_approval": True,
                "evidence": f"{p['count']} sent messages share this opener",
            })

    for r in out:
        r.setdefault("status", "proposed")
    return out


def icp_learning_report(store) -> dict:
    """What the funnel data says about the ICP, plus a recommendation.
    The recommendation is a decision FOR THE HUMAN to accept or reject - the
    ICP is never rewritten automatically."""
    icp = load_icp()
    funnel = funnel_analysis(store)
    segments = segment_performance(store)
    calibration = {}
    try:
        from ..calibration import build_calibration_report
        calibration = build_calibration_report(store)
    except Exception:
        calibration = {}

    activated = funnel["totals"].get("activated", 0)
    customers = funnel["totals"].get("customer", 0)

    recommendation = None
    if activated == 0 and customers == 0:
        recommendation = {
            "type": "insufficient_data",
            "recommendation": "Collect more outreach data before revising ICP.",
            "accept": False,
        }
    else:
        # Which segments actually activated - if any segment over-delivers
        # relative to its share of contacts, it is a candidate for expansion.
        best = None
        for s in segments:
            act = s["activation_rate"]
            if act["sufficient"] and act["rate"] > 0:
                if best is None or act["rate"] > best["activation_rate"]["rate"]:
                    best = s
        if best:
            recommendation = {
                "type": "expand_segment",
                "recommendation": (
                    f"Segment '{best['segment']}' showed {best['activation_rate']['rate']:.0%} "
                    f"activation. Consider expanding its target quota "
                    f"(human decision required)."),
                "segment": best["segment"],
                "accept": None,  # pending human accept/reject
            }
        else:
            recommendation = {
                "type": "confirm_current_icp",
                "recommendation": "No segment beats the rest yet; keep the current ICP.",
                "accept": True,
            }

    return {
        "icp_name": icp.get("name", "icp"),
        "funnel": funnel,
        "segment_performance": segments,
        "calibration": calibration,
        "recommendation": recommendation,
    }
