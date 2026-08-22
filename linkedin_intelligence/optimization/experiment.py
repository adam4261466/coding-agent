"""Experiments: register, evaluate, and track. An experiment compares control
vs variant on a primary metric with a minimum sample. No experiment changes
the system - it only reports a verdict the human acts on."""

import uuid
from datetime import datetime, timezone

from ..utils import load_optimization
from .evaluator import two_proportion_test, wilson_ci

CFG = load_optimization()
MIN_SAMPLE = int(CFG.get("minimum_sample", 20))


def create_experiment(store, hypothesis: str, control: str, variant: str,
                      segment: str = None, primary_metric: str = "activated_users",
                      minimum_sample: int = MIN_SAMPLE) -> dict:
    exp = {
        "experiment_id": "exp_" + uuid.uuid4().hex[:6],
        "hypothesis": hypothesis,
        "control": control,
        "variant": variant,
        "segment": segment,
        "primary_metric": primary_metric,
        "minimum_sample": minimum_sample,
        "status": "running",
        "result": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store.save_experiment(exp)
    return exp


def metrics_for_arm(store, arm: str) -> dict:
    """Pull (ok, n) for one arm (a strategy name or campaign id) on the
    primary observable metric: activated users over contacted."""
    from ..outreach.analytics import per_strategy, per_segment
    n = ok = 0
    for strat, m in per_strategy(store).items():
        if strat == arm:
            n = m.get("contacted", 0)
            ok = m.get("activated", 0)
    if n == 0:
        for seg, m in per_segment(store).items():
            if seg == arm:
                n = m.get("contacted", 0)
                ok = m.get("activated", 0)
    return {"ok": ok, "n": n}


def evaluate_experiment(store, experiment_id: str) -> dict:
    exp = store.get_experiment(experiment_id)
    if not exp:
        raise ValueError(f"no experiment {experiment_id}")
    control = metrics_for_arm(store, exp.get("control", ""))
    variant = metrics_for_arm(store, exp.get("variant", ""))
    result = two_proportion_test(variant["ok"], variant["n"],
                                 control["ok"], control["n"],
                                 min_effect=float(CFG.get("min_effect_to_claim", 0.05)))
    result["control"] = control
    result["variant"] = variant
    result["primary_metric"] = exp.get("primary_metric")
    status = "concluded" if result["verdict"] != "insufficient_data" else "running"
    store.set_experiment_result(experiment_id, result, status=status)
    exp = store.get_experiment(experiment_id)
    return exp


def summarize(store) -> list:
    out = []
    for e in store.experiments():
        out.append({
            "experiment_id": e["experiment_id"],
            "hypothesis": e.get("hypothesis"),
            "control": e.get("control"),
            "variant": e.get("variant"),
            "segment": e.get("segment"),
            "primary_metric": e.get("primary_metric"),
            "minimum_sample": e.get("minimum_sample"),
            "status": e.get("status"),
            "result": e.get("result", {}).get("verdict"),
        })
    return out
