"""Strategy performance: funnel metrics per outreach strategy, with the same
statistical guardrails as segments."""

from ..outreach.analytics import per_strategy
from .evaluator import annotated


def strategy_performance(store, campaign_id: str = None) -> list:
    rows = []
    for strat, m in per_strategy(store, campaign_id).items():
        rows.append({
            "strategy": strat,
            "metrics": m,
            "reply_rate": annotated(m, "replied"),
            "positive_reply_rate": annotated(m, "positive_replies"),
            "activation_rate": annotated(m, "activated"),
        })
    rows.sort(key=lambda r: r["metrics"].get("contacted", 0), reverse=True)
    return rows


def best_strategy(store, campaign_id: str = None) -> dict:
    best = None
    for r in strategy_performance(store, campaign_id):
        act = r["activation_rate"]
        if not act["sufficient"]:
            continue
        if best is None or act["rate"] > best["activation_rate"]["rate"]:
            best = r
    return best
