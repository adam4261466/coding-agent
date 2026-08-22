"""Final Phase 1/2 report: funnel, exclusions by reason, distributions,
research coverage, qualification confidence, and unresolved/unknown fields.

This is the report that tells you whether the scoring system is actually
useful - e.g. "38 high ICP -> 31 relevant -> 17 strong problem fit -> 8
demonstrated pain" - rather than trusting the raw high-relevance count.
"""

import os
from collections import Counter

from .utils import REPORTS_DIR, INTELLIGENCE_DIR, load_icp, save_json
from .research.pain_state import pain_state


def _score_buckets(score: float):
    if score is None:
        return "no_score"
    if score >= 80:
        return "80-100"
    if score >= 70:
        return "70-79"
    if score >= 55:
        return "55-69"
    if score >= 40:
        return "40-54"
    if score >= 20:
        return "20-39"
    return "0-19"


def build_report_data(store, top_n: int = None) -> dict:
    icp = load_icp()
    hi = icp.get("high_relevance_threshold", 55)
    ready_review = float(icp.get("qualification", {}).get("ready_for_review", 70))
    strong_pf = float(icp.get("qualification", {}).get("demonstrated_pain", 60))

    prospects = store.prospects()
    quals = store.qualifications()
    qual_by_pid = {}
    for q in quals:
        qual_by_pid.setdefault(q["prospect_id"], q)  # first wins (oldest)

    # Funnel (offline phase-1 numbers then research-verified numbers).
    high_icp = [p for p in prospects if (p.get("total_score") or 0) >= hi]
    qualified = [p for p in high_icp if p["prospect_id"] in qual_by_pid] \
        if high_icp else [p for p in prospects if p["prospect_id"] in qual_by_pid]

    relevant_after_research = [p for p in qualified
                               if (qual_by_pid[p["prospect_id"]].get("fit_score") or 0) >= ready_review
                               or p.get("status") in ("ready_for_human_review",
                                                      "ready_for_outreach", "follow_up")]
    strong_problem_fit = [p for p in qualified
                          if (p.get("problem_fit_score") or 0) >= strong_pf]
    demonstrated = [p for p in strong_problem_fit
                    if p.get("commercial_intent") in ("explicit", "possible")
                    or (p.get("conversation_intelligence") or {}).get("pain_signal") == "explicit"]

    # Exclusions by reason.
    exclusions = Counter()
    for e in store.conn.execute("SELECT reasons FROM exclusions").fetchall():
        for r in json_loads(e["reasons"]):
            exclusions[r] += 1

    # Raw/unique counts: raw stored by Phase 1, unique = prospects in DB.
    unique_prospects = len(prospects)
    excluded_count = store.conn.execute("SELECT COUNT(*) FROM exclusions").fetchone()[0]
    raw_meta = store.get_meta("raw_connections")
    raw_connections = int(raw_meta) if raw_meta else unique_prospects + excluded_count

    # Score distribution.
    dist = Counter(_score_buckets(p.get("total_score")) for p in prospects)

    # Segment distribution.
    segs = Counter()
    for p in prospects:
        for s in p.get("segments", []):
            segs[s] += 1

    # Unknown / unresolved fields.
    unknown = {
        "problem_fit_unknown": sum(1 for p in prospects if p.get("problem_fit_score") is None),
        "commercial_intent_unknown": sum(
            1 for p in prospects
            if p.get("commercial_intent") in ("unknown", "none") and p.get("conversation_count", 0) == 0),
        "role_category_unknown": sum(1 for p in prospects if p.get("role_category") == "unknown"),
    }

    # Pain-state semantics: "not researched" is NOT "no pain".
    pain_states = Counter(pain_state(p, qual_by_pid.get(p["prospect_id"]))
                          for p in prospects)

    # Segmentation coverage and why prospects are unsegmented.
    seg_status = Counter(p.get("segmentation_status", "segmented")
                         for p in prospects)
    missing_counter = Counter()
    for p in prospects:
        if p.get("segmentation_status") == "unsegmented":
            for m in p.get("missing_signals", []):
                missing_counter[m] += 1

    # Dimension funnel: each score dimension kept separate, never collapsed.
    def _above(p, key, thr):
        v = p.get(key)
        return v is not None and v >= thr
    dimension_funnel = {
        "role_fit_60plus": sum(1 for p in prospects if _above(p, "role_fit", 60)),
        "company_fit_60plus": sum(1 for p in prospects if _above(p, "company_fit", 60)),
        "relationship_60plus": sum(1 for p in prospects if _above(p, "relationship_score", 60)),
        "engagement_40plus": sum(1 for p in prospects if _above(p, "engagement_score", 40)),
        "buying_signal_possible_plus": sum(
            1 for p in prospects if p.get("commercial_intent") in ("explicit", "possible")),
        "problem_fit_demonstrated": sum(1 for p in prospects if _above(p, "problem_fit_score", strong_pf)),
    }
    high_icp_total = [p for p in high_icp]
    high_icp_unknown_pain = sum(1 for p in high_icp_total
                                if p.get("problem_fit_score") is None)

    contradictions = sum(len(q.get("contradictions") or [])
                         for q in quals)

    # Research coverage.
    tasks = store.research_tasks()
    task_status = Counter(t["status"] for t in tasks)
    mode_counts = Counter(q.get("research_mode", "unknown") for q in quals)
    conf_vals = [q.get("confidence") for q in quals if q.get("confidence") is not None]
    conf_unknown = sum(1 for q in quals if q.get("confidence") is None or q.get("confidence") == 0)

    return {
        "totals": {
            "prospects_in_db": len(prospects),
            "raw_connections": raw_connections,
            "unique_prospects": unique_prospects,
            "excluded": sum(exclusions.values()),
            "research_tasks": len(tasks),
            "qualified": len(quals),
        },
        "funnel": {
            "high_icp": len(high_icp),
            "high_icp_with_unknown_pain": high_icp_unknown_pain,
            "relevant_after_research": len(relevant_after_research),
            "strong_problem_fit": len(strong_problem_fit),
            "demonstrated_pain_or_interest": len(demonstrated),
        },
        "dimension_funnel": dimension_funnel,
        "pain_states": dict(pain_states),
        "segmentation_coverage": {
            "segmented": seg_status.get("segmented", 0),
            "unsegmented": seg_status.get("unsegmented", 0),
            "unsegmented_pct": round(
                seg_status.get("unsegmented", 0) / max(1, sum(seg_status.values())) * 100, 1),
            "top_missing_signals": dict(missing_counter.most_common(8)),
        },
        "exclusions_by_reason": dict(exclusions.most_common()),
        "score_distribution": dict(dist.most_common()),
        "segment_distribution": dict(segs.most_common()),
        "unknown_fields": unknown,
        "research_coverage": {
            "tasks_by_status": dict(task_status),
            "research_modes": dict(mode_counts),
            "avg_qualification_confidence": round(sum(conf_vals) / len(conf_vals), 2) if conf_vals else None,
            "qualifications_without_confidence": conf_unknown,
        },
        "qualification_quality": {
            "contradictions_flagged": contradictions,
            "qualified_with_contradictions": sum(
                1 for q in quals if q.get("contradictions")),
        },
        "top_prospects": [
            {
                "name": p["full_name"], "total_score": p.get("total_score"),
                "role_category": p.get("role_category"), "company": p.get("current_company"),
                "problem_fit": p.get("problem_fit_score"), "status": p.get("status"),
            }
            for p in sorted(prospects, key=lambda x: x.get("total_score") or 0, reverse=True)[:top_n or 20]
        ],
    }


def json_loads(text):
    import json
    try:
        return json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def write_final_report(store, path_dir: str = None, top_n: int = None) -> str:
    data = build_report_data(store, top_n=top_n)
    path = os.path.join(path_dir or REPORTS_DIR, "final_report.json")
    save_json(data, path)

    f = data["funnel"]
    t = data["totals"]
    lines = [
        "FINAL INTELLIGENCE REPORT (Phase 1 + Phase 2)",
        "=" * 46,
        "",
        "TOTALS",
        f"  Prospects in DB:          {t['prospects_in_db']}",
        f"  Excluded:                 {t['excluded']}",
        f"  Research tasks:           {t['research_tasks']}",
        f"  Qualified:                {t['qualified']}",
        "",
        "FUNNEL (is the scoring useful?)",
        f"  High ICP fit:             {f['high_icp']}",
        f"  ...with pain unknown:     {f['high_icp_with_unknown_pain']}",
        f"  Relevant after research:  {f['relevant_after_research']}",
        f"  Strong problem fit:       {f['strong_problem_fit']}",
        f"  Demonstrated pain/interest: {f['demonstrated_pain_or_interest']}",
        "",
        "DIMENSIONS (kept separate, never collapsed)",
    ]
    for k, n in data["dimension_funnel"].items():
        lines.append(f"  {n:>4}  {k}")
    lines += ["", "PAIN STATES (unknown is NOT 'no pain')"]
    for state, n in data["pain_states"].items():
        lines.append(f"  {n:>4}  {state}")
    lines += ["", "SEGMENTATION COVERAGE"]
    sc = data["segmentation_coverage"]
    lines.append(f"  Segmented:                {sc['segmented']}")
    lines.append(f"  Unsegmented:              {sc['unsegmented']}  ({sc['unsegmented_pct']}%)")
    lines.append("  Top missing signals:")
    for sig, n in sc["top_missing_signals"].items():
        lines.append(f"    {n:>3}  {sig}")
    lines += ["", "EXCLUSIONS BY REASON"]
    for reason, n in data["exclusions_by_reason"].items():
        lines.append(f"  {n:>3}  {reason}")
    lines += ["", "SCORE DISTRIBUTION"]
    for bucket, n in data["score_distribution"].items():
        lines.append(f"  {n:>3}  {bucket}")
    lines += ["", "SEGMENTS"]
    for seg, n in data["segment_distribution"].items():
        lines.append(f"  {n:>3}  {seg}")
    lines += ["", "UNKNOWN / UNRESOLVED"]
    for k, n in data["unknown_fields"].items():
        lines.append(f"  {n:>3}  {k}")
    lines += ["", "QUALIFICATION QUALITY"]
    for k, v in data["qualification_quality"].items():
        lines.append(f"  {v}  {k}")
    lines += ["", "RESEARCH COVERAGE"]
    for k, v in data["research_coverage"].items():
        lines.append(f"  {v}  {k}")

    md_path = os.path.join(path_dir or REPORTS_DIR, "final_report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return md_path
