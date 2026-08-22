"""Segment performance: funnel + reply metrics per ICP segment, annotated with
confidence intervals and sample-sufficiency. Numbers come from the outreach
analytics module; this module only adds statistics and verdicts."""

from ..outreach.analytics import per_segment, metrics_for
from ..store import Store
from .evaluator import annotated


def segment_performance(store, campaign_id: str = None) -> list:
    rows = []
    for seg, m in per_segment(store, campaign_id).items():
        rows.append({
            "segment": seg,
            "metrics": m,
            "reply_rate": annotated(m, "replied"),
            "positive_reply_rate": annotated(m, "positive_replies"),
            "activation_rate": annotated(m, "activated"),
            "customer_rate": annotated(m, "customer"),
        })
    rows.sort(key=lambda r: r["metrics"].get("contacted", 0), reverse=True)
    return rows


def best_segment(store, campaign_id: str = None) -> dict:
    """The segment with the highest activation rate that has a sufficient
    sample; None when no segment qualifies (so nobody claims a winner)."""
    best = None
    for r in segment_performance(store, campaign_id):
        act = r["activation_rate"]
        if not act["sufficient"]:
            continue
        if best is None or act["rate"] > best["activation_rate"]["rate"]:
            best = r
    return best
