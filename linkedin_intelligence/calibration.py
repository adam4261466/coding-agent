"""Calibration report: does the pipeline's scoring/threshold actually predict
which prospects a human approves?

Ground truth comes ONLY from human review decisions (store.human_feedback).
Model predictions are the stored qualifications. A model decision counts as a
positive prediction when the recommendation is READY_FOR_HUMAN_REVIEW /
READY_FOR_OUTREACH; human ground truth counts a prospect as positive when the
reviewer approved it (ready_for_outreach), negative when rejected
(do_not_contact/skipped), and "more research" is treated as unlabelled.

Report includes:
  - precision / recall of the model's binary decision
  - a threshold sweep on fit_score against the same ground truth, so you can
    see whether 48 (or another threshold) is the right cut-off
  - contradiction stats, segmentation coverage, pain-state distribution
"""

import os
from collections import Counter

from .utils import REPORTS_DIR, load_icp, save_json
from .research.pain_state import pain_state

APPROVAL_PREDICTIONS = ("READY_FOR_HUMAN_REVIEW", "READY_FOR_OUTREACH")
POSITIVE_HUMAN = {"ready_for_outreach"}
NEGATIVE_HUMAN = {"do_not_contact", "skipped"}
NEUTRAL_HUMAN = {"research", "follow_up"}


def _human_label(feedback_rows: list):
    """Latest human decision per prospect; None if never reviewed."""
    latest = {}
    for f in feedback_rows:
        latest[f["prospect_id"]] = f["action"]
    return latest


def _binaries(q_pred_pos, human_pos, human_neg):
    tp = fp = fn = tn = 0
    for pid, pred in q_pred_pos.items():
        if pred:
            tp += pid in human_pos
            fp += pid in human_neg
        else:
            fn += pid in human_pos
            tn += pid in human_neg
    return tp, fp, fn, tn


def _prec_rec(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return precision, recall


def build_calibration_report(store) -> dict:
    icp = load_icp()
    quals = store.qualifications()
    feedback = store.feedback()
    prospects = store.prospects()
    by_pid = {p["prospect_id"]: p for p in prospects}
    human = _human_label(feedback)

    # Pair qualification with ground truth where it exists.
    q_by_pid = {}
    for q in quals:
        q_by_pid.setdefault(q["prospect_id"], q)  # oldest qualification wins

    labelled = {pid: h for pid, h in human.items()
                if pid in q_by_pid and h in POSITIVE_HUMAN | NEGATIVE_HUMAN}
    human_pos = {pid for pid, h in labelled.items() if h in POSITIVE_HUMAN}
    human_neg = {pid for pid, h in labelled.items() if h in NEGATIVE_HUMAN}

    pred_pos = {pid: q.get("recommended_next_action") in APPROVAL_PREDICTIONS
                for pid, q in q_by_pid.items()}

    tp, fp, fn, tn = _binaries(pred_pos, human_pos, human_neg)
    precision, recall = _prec_rec(tp, fp, fn)

    # Threshold sweep of fit_score against the same ground truth.
    sweep = []
    scored = {pid: (q.get("fit_score") or 0) for pid, q in q_by_pid.items()}
    for thr in range(35, 71, 5):
        pos = {pid: s >= thr for pid, s in scored.items()}
        t, f, n, _ = _binaries(pos, human_pos, human_neg)
        p, r = _prec_rec(t, f, n)
        sweep.append({"threshold": thr, "precision": p, "recall": r,
                      "true_pos": t, "false_pos": f, "false_neg": n,
                      "n_positive": sum(1 for v in pos.values() if v)})

    # Contradictions flagged by validate_qualification.
    contradictions = []
    for q in quals:
        for c in (q.get("contradictions") or []):
            contradictions.append({"prospect_id": q["prospect_id"],
                                   "contradiction": c,
                                   "action": q.get("recommended_next_action"),
                                   "fit_score": q.get("fit_score"),
                                   "confidence": q.get("confidence")})

    # Segmentation coverage + pain-state distribution.
    seg_status = Counter(p.get("segmentation_status", "segmented")
                         for p in prospects)
    pain_states = Counter(pain_state(p, q_by_pid.get(p["prospect_id"]))
                          for p in prospects)
    unsegmented = [p for p in prospects
                   if p.get("segmentation_status") == "unsegmented"]
    missing_counter = Counter()
    for p in unsegmented:
        for m in p.get("missing_signals", []):
            missing_counter[m] += 1

    # Acquisition vs discovery outcomes.
    by_stage = {}
    for q in quals:
        stage = q.get("research_stage", "acquisition")
        by_stage.setdefault(stage, {"n": 0, "approved": 0,
                                    "high_fit": 0, "avg_conf": []})
        by_stage[stage]["n"] += 1
        if q.get("recommended_next_action") in APPROVAL_PREDICTIONS:
            by_stage[stage]["approved"] += 1
        if (q.get("fit_score") or 0) >= float(
                icp.get("qualification", {}).get("ready_for_review", 70)):
            by_stage[stage]["high_fit"] += 1
        if q.get("confidence") is not None:
            by_stage[stage]["avg_conf"].append(q["confidence"])
    for s in by_stage.values():
        s["avg_conf"] = round(sum(s["avg_conf"]) / len(s["avg_conf"]), 2) \
            if s["avg_conf"] else None

    return {
        "ground_truth": {
            "human_reviewed": len(human),
            "approved": len(human_pos),
            "rejected": len(human_neg),
            "more_research_or_other": sum(
                1 for h in human.values() if h not in POSITIVE_HUMAN | NEGATIVE_HUMAN),
        },
        "model_decision": {
            "n_qualified": len(quals),
            "predicted_positive": sum(1 for v in pred_pos.values() if v),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "true_negatives": tn,
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
        },
        "threshold_sweep": sweep,
        "contradictions": {
            "count": len(contradictions),
            "items": contradictions[:50],
        },
        "segmentation_coverage": {
            "segmented": seg_status.get("segmented", 0),
            "unsegmented": seg_status.get("unsegmented", 0),
            "unsegmented_pct": round(
                seg_status.get("unsegmented", 0) / max(1, sum(seg_status.values())) * 100, 1),
            "top_missing_signals": dict(missing_counter.most_common(8)),
        },
        "pain_states": dict(pain_states),
        "by_stage": by_stage,
    }


def write_calibration_report(store, path_dir: str = None) -> str:
    data = build_calibration_report(store)
    path = os.path.join(path_dir or REPORTS_DIR, "calibration_report.json")
    save_json(data, path)

    m = data["model_decision"]
    gt = data["ground_truth"]
    lines = [
        "CALIBRATION REPORT (model vs human review)",
        "=" * 46,
        "",
        "GROUND TRUTH (human review)",
        f"  Reviewed:                 {gt['human_reviewed']}",
        f"  Approved (positive):      {gt['approved']}",
        f"  Rejected (negative):      {gt['rejected']}",
        f"  More research / other:    {gt['more_research_or_other']}",
        "",
        "MODEL DECISION (READY_FOR_HUMAN_REVIEW / READY_FOR_OUTREACH)",
        f"  Qualified:                {m['n_qualified']}",
        f"  Predicted positive:       {m['predicted_positive']}",
        f"  TP / FP / FN / TN:        {m['true_positives']} / {m['false_positives']} / "
        f"{m['false_negatives']} / {m['true_negatives']}",
        f"  Precision:                {m['precision']}",
        f"  Recall:                   {m['recall']}",
        "",
        "FIT_SCORE THRESHOLD SWEEP (vs human ground truth)",
    ]
    for row in data["threshold_sweep"]:
        p = row["precision"] if row["precision"] is not None else "-"
        r = row["recall"] if row["recall"] is not None else "-"
        lines.append(f"  >= {row['threshold']:>2}:  precision {p}  recall {r}  "
                     f"(TP {row['true_pos']} FP {row['false_pos']} FN {row['false_neg']})")
    lines += [
        "",
        "CONTRADICTIONS (model output vs its own numbers)",
        f"  Flagged:                  {data['contradictions']['count']}",
    ]
    for c in data["contradictions"]["items"][:15]:
        lines.append(f"    {c['prospect_id']}  fit={c['fit_score']}  {c['contradiction']}")
    lines += [
        "",
        "SEGMENTATION COVERAGE",
        f"  Segmented:                {data['segmentation_coverage']['segmented']}",
        f"  Unsegmented:              {data['segmentation_coverage']['unsegmented']}"
        f"  ({data['segmentation_coverage']['unsegmented_pct']}%)",
        "  Top missing signals:",
    ]
    for sig, n in data["segmentation_coverage"]["top_missing_signals"].items():
        lines.append(f"    {n:>3}  {sig}")
    lines += ["", "PAIN STATES (unknown is NOT 'no pain')"]
    for state, n in data["pain_states"].items():
        lines.append(f"  {n:>3}  {state}")
    lines += ["", "ACQUISITION vs DISCOVERY"]
    for stage, s in data["by_stage"].items():
        lines.append(f"  {stage}: n={s['n']} approved={s['approved']} "
                     f"high_fit={s['high_fit']} avg_conf={s['avg_conf']}")

    md_path = os.path.join(path_dir or REPORTS_DIR, "calibration_report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return md_path
