"""Hypothesis generation - deterministic, data-driven. Hypotheses are
suggestions for the human; they never change behavior by themselves."""

import uuid
from datetime import datetime, timezone


def generate_hypotheses(store, funnel: dict = None,
                        segments: list = None, strategies: list = None) -> list:
    """Build candidate experiments from observed gaps. Only gaps that exist in
    the DATA become hypotheses - nothing is invented."""
    from .funnel_analysis import funnel_analysis, biggest_gap_between_segments
    from .segment_performance import segment_performance
    from .strategy_performance import strategy_performance

    funnel = funnel or funnel_analysis(store)
    segments = segments if segments is not None else segment_performance(store)
    strategies = strategies if strategies is not None else strategy_performance(store)

    out = []

    bn = funnel.get("bottleneck")
    if bn and bn.get("n_from", 0) >= 10:
        out.append({
            "hypothesis": (f"The funnel drops most at {bn['from']} -> {bn['to']} "
                           f"({bn['rate']:.0%} conversion). Improving this step "
                           "should lift the whole funnel."),
            "metric": f"rate {bn['from']}->{bn['to']}",
            "evidence": f"{bn['n_to']}/{bn['n_from']} converted",
            "experiment_hint": f"test a change aimed at the {bn['to']} stage",
        })

    gap = biggest_gap_between_segments(store)
    if gap and gap["gap"] >= 0.1:
        out.append({
            "hypothesis": (f"Segment '{gap['high']}' activates at {gap['high_rate']:.0%} "
                           f"vs '{gap['low']}' at {gap['low_rate']:.0%}. "
                           "Reprioritizing toward the better segment may raise "
                           "activation, but needs a controlled experiment."),
            "metric": "activation_rate",
            "evidence": f"gap={gap['gap']:.0%}",
            "experiment_hint": f"A/B: {gap['low']} vs {gap['high']} targeting",
        })

    sufficient = [s for s in strategies
                  if s["activation_rate"]["sufficient"]]
    if len(sufficient) >= 2:
        s0 = max(sufficient, key=lambda s: s["activation_rate"]["rate"])
        s1 = min(sufficient, key=lambda s: s["activation_rate"]["rate"])
        if s0["activation_rate"]["rate"] - s1["activation_rate"]["rate"] >= 0.05:
            out.append({
                "hypothesis": (f"Strategy '{s0['strategy']}' shows higher activation "
                               f"({s0['activation_rate']['rate']:.0%}) than "
                               f"'{s1['strategy']}' ({s1['activation_rate']['rate']:.0%})."),
                "metric": "activation_rate",
                "evidence": f"n={s0['metrics'].get('contacted')} vs {s1['metrics'].get('contacted')}",
                "experiment_hint": f"A/B: {s0['strategy']} vs {s1['strategy']}",
            })

    for h in out:
        h["hypothesis_id"] = "hyp_" + uuid.uuid4().hex[:6]
        h["created_at"] = datetime.now(timezone.utc).isoformat()
        h["status"] = "suggested"
    return out
