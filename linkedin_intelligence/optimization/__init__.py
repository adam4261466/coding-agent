"""Phase 4: growth optimization engine.

Computes performance from real funnel data (never invented), compares with
confidence intervals and minimum samples, proposes experiments and
recommendations, and produces an ICP learning report. Every recommendation
requires human approval; nothing changes automatically.
"""

from . import evaluator, segment_performance, strategy_performance, \
    message_performance, funnel_analysis, experiment, hypothesis, \
    recommendations, calibration  # noqa: F401

__all__ = [
    "evaluator", "segment_performance", "strategy_performance",
    "message_performance", "funnel_analysis", "experiment", "hypothesis",
    "recommendations", "calibration",
]
