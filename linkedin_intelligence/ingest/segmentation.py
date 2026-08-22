"""Segment assignment runs AFTER scoring and BEFORE Phase 2 research.

Every prospect is assigned to every segment whose rules ALL match, so a
prospect can belong to several candidate pools. A prospect with no match gets
"unsegmented" - nothing silently disappears from the pipeline.

Each prospect also gets introspection state so the funnel can be audited:
  - segmentation_status: "segmented" | "unsegmented"
  - missing_signals: for unsegmented prospects, the rules that failed for the
    closest matching segment (e.g. "score below 50", "role category unknown").
"""

from ..utils import load_segments as _load_segments

RULE_LABELS = {
    "role_category": "role category match",
    "company_type": "company type match",
    "min_total_score": "minimum ICP score",
    "max_total_score": "maximum ICP score",
    "min_relationship_score": "relationship score",
    "conversation_count_min": "prior conversation",
    "engagement_min": "engagement",
}


def load_segment_defs() -> list:
    return _load_segments().get("segments", [])


def _rule_detail(p: dict, rules: dict):
    """(matches, failed_keys) for ONE rule set."""
    failed = []
    if "role_category" in rules and p.get("role_category") not in rules["role_category"]:
        failed.append("role_category")
    if "company_type" in rules and p.get("company_type") not in rules["company_type"]:
        failed.append("company_type")
    if rules.get("min_total_score") is not None and \
            (p.get("total_score") or 0) < rules["min_total_score"]:
        failed.append("min_total_score")
    if rules.get("max_total_score") is not None and \
            (p.get("total_score") or 0) > rules["max_total_score"]:
        failed.append("max_total_score")
    if rules.get("min_relationship_score") is not None and \
            (p.get("relationship_score") or 0) < rules["min_relationship_score"]:
        failed.append("min_relationship_score")
    if rules.get("conversation_count_min") is not None and \
            (p.get("conversation_count") or 0) < rules["conversation_count_min"]:
        failed.append("conversation_count_min")
    if rules.get("engagement_min") is not None and \
            (p.get("engagement_score") or 0) < rules["engagement_min"]:
        failed.append("engagement_min")
    return len(failed) == 0, failed


def _matches(p: dict, rules: dict) -> bool:
    ok, _ = _rule_detail(p, rules)
    return ok


def _missing_for(p: dict, defs: list) -> list:
    """Diagnostics for an unsegmented prospect: failed rules of the closest
    segment, as human-readable signals. Empty if the prospect matched one."""
    best = None
    for d in defs:
        rules = d.get("rules", {})
        ok, failed = _rule_detail(p, rules)
        if ok:
            return []
        if best is None or len(failed) < best[1]:
            best = (d["name"], len(failed), failed)
    if best is None:
        return ["no segment rules defined"]
    missing = [RULE_LABELS.get(k, k) for k in best[2]]
    if p.get("role_category") == "unknown":
        missing.append("role category unknown")
    if p.get("company_type") == "unknown":
        missing.append("company type unknown")
    if p.get("conversation_count", 0) == 0:
        missing.append("no prior conversation")
    return list(dict.fromkeys(missing))


def assign_segments(prospects: list, defs: list = None) -> list:
    """Mutates each prospect's `segments` list and returns the segment index."""
    defs = defs or load_segment_defs()
    index = {}
    for p in prospects:
        matched = [d["name"] for d in defs if _matches(p, d.get("rules", {}))]
        p["segments"] = matched or ["unsegmented"]
        if matched:
            p["segmentation_status"] = "segmented"
            p["missing_signals"] = []
        else:
            p["segmentation_status"] = "unsegmented"
            p["missing_signals"] = _missing_for(p, defs)
        for name in p["segments"]:
            index.setdefault(name, []).append(p["prospect_id"])
    return index


def backfill_segmentation(store) -> int:
    """Recompute segmentation introspection on records already in the DB
    (e.g. after upgrading, without resetting Phase 2 state). Deterministic."""
    prospects = store.prospects()
    if not prospects:
        return 0
    assign_segments(prospects)
    store.upsert_prospects(prospects)
    return len(prospects)
