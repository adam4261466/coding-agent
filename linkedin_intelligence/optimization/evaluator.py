"""Statistical evaluator: the ONLY place numbers are computed and compared.

Every comparison is guarded by minimum sample sizes and confidence intervals.
A 2/10 (20%) rate is NEVER called better than 12/100 (12%) - the rules decide,
not the LLM. The LLM may interpret these verdicts, but it must never produce
the numbers itself.
"""

import math

from ..utils import load_optimization

_CFG = load_optimization()


def wilson_ci(ok: int, n: int, z: float = None) -> dict:
    """Wilson score interval for a proportion (ok/n). Handles n=0 and edge
    cases without breaking."""
    z = z if z is not None else float(_CFG.get("z_confidence", 1.96))
    if n <= 0:
        return {"rate": 0.0, "ci_low": 0.0, "ci_high": 0.0, "n": 0}
    p = ok / n
    if p <= 0:
        return {"rate": 0.0, "ci_low": 0.0, "ci_high": 0.0, "n": n}
    if p >= 1:
        # Approximate upper bound for 100% with a capped interval.
        se = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) \
            if n else 0.0
        return {"rate": 1.0, "ci_low": max(0.0, 1.0 - 1.0 / n) if n else 0.0,
                "ci_high": 1.0, "n": n}
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = (z * math.sqrt((p * (1 - p) + z ** 2 / (4 * n)) / n)) / denom
    return {
        "rate": round(p, 4),
        "ci_low": round(max(0.0, centre - half), 4),
        "ci_high": round(min(1.0, centre + half), 4),
        "n": n,
    }


def two_proportion_test(a_ok: int, a_n: int, b_ok: int, b_n: int,
                        min_effect: float = None) -> dict:
    """Compare variant (a) vs control (b) rates. Returns a verdict:
    "winner", "no_significant_difference", or "insufficient_data". Never
    declares a winner without the minimum sample AND a significant effect."""
    min_effect = min_effect if min_effect is not None \
        else float(_CFG.get("min_effect_to_claim", 0.05))
    min_sample = int(_CFG.get("minimum_sample", 20))
    if a_n < min_sample or b_n < min_sample:
        return {
            "verdict": "insufficient_data",
            "a": wilson_ci(a_ok, a_n), "b": wilson_ci(b_ok, b_n),
            "reason": f"sample {min(a_n, b_n)} < minimum {min_sample}",
        }
    if a_n == 0 or b_n == 0:
        return {"verdict": "insufficient_data",
                "a": wilson_ci(a_ok, a_n), "b": wilson_ci(b_ok, b_n),
                "reason": "zero denominator"}

    pa, pb = a_ok / a_n, b_ok / b_n
    # Pooled two-proportion z-test.
    p = (a_ok + b_ok) / (a_n + b_n)
    se = math.sqrt(p * (1 - p) * (1 / a_n + 1 / b_n))
    z = (pa - pb) / se if se > 0 else 0.0
    significant = abs(z) > float(_CFG.get("z_confidence", 1.96))

    if significant and (pa - pb) >= min_effect:
        verdict = "winner"
    elif significant and (pb - pa) >= min_effect:
        verdict = "loser"
    else:
        verdict = "no_significant_difference"

    return {
        "verdict": verdict,
        "a": {**wilson_ci(a_ok, a_n), "ok": a_ok},
        "b": {**wilson_ci(b_ok, b_n), "ok": b_ok},
        "z": round(z, 3),
        "reason": (f"a={pa:.1%} vs b={pb:.1%}, z={z:.2f}") if not
        (pa == pb and z == 0.0) else "identical rates",
    }


def sample_ok(metrics: dict, key: str) -> bool:
    return int(metrics.get(key + "_n", 0) or 0) >= int(_CFG.get("minimum_sample", 20))


def annotated(metrics: dict, key: str) -> dict:
    """Attach a CI + sample-sufficiency verdict for one rate key."""
    n = int(metrics.get("contacted", 0))
    ok = int(metrics.get(key, 0))
    ci = wilson_ci(ok, n)
    return {
        **ci,
        "sufficient": n >= int(_CFG.get("minimum_sample", 20)),
    }
