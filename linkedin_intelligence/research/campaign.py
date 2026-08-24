"""Build structured research tasks from the scored prospect pool.

Two batch types, mirroring the two research goals:
  - exploitation (stage "acquisition"): the diversified top-N by score. Finds
    the best candidates FAST, at the cost of staying within known segments.
  - exploration   (stage "discovery"): spread across roles, deliberately
    pulling in UNSEGMENTED / not-yet-researched candidates instead of the
    highest scores. Finds hidden fit, never a replacement for the top-N.

Every selected candidate gets `selection_reasons` explaining exactly why it
entered the batch, and the task records its stage so downstream logic can
compare acquisition vs discovery outcomes.
"""


from ..timeutil import iso as _now_iso
import uuid
from datetime import datetime, timezone
from collections import Counter

from ..utils import load_icp

EXCLUDED_STATUSES = {"do_not_contact", "not_relevant", "already_customer",
                     "declined", "ready_for_outreach", "skipped"}

# Accept both vocabularies; tasks always record the acquisition/discovery stage.
BATCH_TYPES = {"exploitation", "exploration", "acquisition", "discovery"}


def _normalize_batch_type(batch_type: str) -> str:
    if batch_type in ("discovery", "exploration"):
        return "exploration"
    return "exploitation"


def _eligible(prospects):
    return [p for p in prospects if p["status"] not in EXCLUDED_STATUSES]


def _researched_ids(store) -> set:
    """Prospect IDs whose research task is actually done (completed)."""
    done_ids = set()
    for q in store.qualifications():
        # Only count qualifications where a task exists and is done;
        # partial / rule-based fallback quals should not block re-research.
        task_id = q.get("task_id")
        if not task_id:
            continue
        task = store.get_research_task(task_id)
        if task and task.get("status") == "done":
            done_ids.add(q["prospect_id"])
    return done_ids


def _pass_score_filter(prospect, min_score):
    if min_score is None:
        return True
    try:
        return float(prospect.get("total_score", 0)) >= float(min_score)
    except (TypeError, ValueError):
        return True


def select_batch(candidates, size, quotas=None, default_quota=2,
                 max_per_company=3, priority_order=None):
    """Pick `size` candidates with segment quotas and per-company caps.

    Returns (selected, reasons_by_prospect_id).
    """
    quotas = dict(quotas or {})
    candidates = list(candidates)
    segments_order = priority_order or list(quotas.keys())
    size = len(candidates) if size is None else size  # None = take all

    # Pre-sort each candidate by score; company cap counts by normalized company.
    by_segment = {}
    for p in candidates:
        for seg in p.get("segments", []):
            by_segment.setdefault(seg, []).append(p)
    for seg, pool in by_segment.items():
        pool.sort(key=lambda x: x.get("total_score", 0), reverse=True)

    selected = []
    seen = set()
    company_counts = Counter()
    reasons = {}

    def _take(p, seg):
        selected.append(p)
        seen.add(p["prospect_id"])
        company_counts[p.get("normalized_company") or ""] += 1
        reasons[p["prospect_id"]] = _why_selected(p, seg)

    # 1. Quota segments first (each segment contributes up to its quota).
    for seg in segments_order:
        quota = int(quotas.get(seg, default_quota))
        if quota <= 0:
            continue
        pool = [p for p in by_segment.get(seg, []) if p["prospect_id"] not in seen]
        taken = 0
        for p in pool:
            if len(selected) >= size:
                break
            if company_counts.get(p.get("normalized_company") or "", 0) >= max_per_company:
                continue
            _take(p, seg)
            taken += 1
            if taken >= quota:
                break
        if len(selected) >= size:
            break

    # 2. Fill remaining slots by overall score (still respecting company caps).
    if len(selected) < size:
        rest = [p for p in sorted(candidates,
                                  key=lambda x: x.get("total_score", 0), reverse=True)
                if p["prospect_id"] not in seen]
        for p in rest:
            if len(selected) >= size:
                break
            if company_counts.get(p.get("normalized_company") or "", 0) >= max_per_company:
                continue
            _take(p, p.get("segments", ["unsegmented"])[0])
    return selected, reasons


def select_exploration_batch(candidates, size, max_per_company=3,
                             researched_ids=None):
    """Pick `size` candidates for DISCOVERY: spread across roles, biased toward
    unsegmented / not-yet-researched prospects instead of the top scores.

    Deterministic: within each role group, unsegmented candidates come first,
    then by ascending score (least-known first). Round-robin across roles with
    per-company caps. Returns (selected, reasons_by_prospect_id).
    """
    researched_ids = researched_ids or set()
    size = len(candidates) if size is None else int(size)  # None = take all

    def _priority(p):
        return (0 if p.get("qualification_fit") is None else 1,
                0 if p.get("segmentation_status") != "segmented" else 1,
                p.get("total_score") or 0)

    groups = {}
    for p in candidates:
        if p["prospect_id"] in researched_ids:
            continue
        key = p.get("role_category") or "unknown"
        groups.setdefault(key, []).append(p)
    for key, pool in groups.items():
        pool.sort(key=_priority)

    selected = []
    seen = set()
    company_counts = Counter()
    reasons = {}
    while len(selected) < size and groups:
        took = False
        for key in list(groups.keys()):
            pool = groups[key]
            # Drop candidates permanently blocked by the company cap so the
            # round-robin keeps moving instead of stalling on them.
            while pool and company_counts.get(
                    pool[0].get("normalized_company") or "", 0) >= max_per_company:
                pool.pop(0)
            if not pool:
                del groups[key]
                continue
            p = pool.pop(0)
            selected.append(p)
            seen.add(p["prospect_id"])
            company_counts[p.get("normalized_company") or ""] += 1
            reasons[p["prospect_id"]] = _why_selected(p, "discovery",
                                                      exploration=True)
            took = True
            if len(selected) >= size:
                break
        if not took:
            break
    return selected, reasons


def _why_selected(prospect: dict, segment: str, exploration: bool = False) -> list:
    if exploration:
        reasons = ["Exploration batch (discovery): hidden-fit search outside top-N"]
    else:
        reasons = ["Existing first-degree connection"]
    if exploration and prospect.get("segmentation_status") == "unsegmented":
        reasons.append("Unsegmented prospect - missing signals need research")
    if prospect.get("role_category") and prospect["role_category"] != "unknown":
        reasons.append(f"Role category '{prospect['role_category']}' matches target segments")
    if prospect.get("company_type") and prospect["company_type"] != "unknown":
        reasons.append(f"Company type '{prospect['company_type']}' matches ICP")
    if prospect.get("conversation_count", 0) > 0:
        reasons.append(f"Prior conversation ({prospect['conversation_count']} thread(s))")
    if segment and segment != "unsegmented":
        reasons.append(f"Belongs to segment '{segment}'")
    if prospect.get("total_score") is not None:
        reasons.append(f"Ordering score {prospect['total_score']}")
    if not prospect.get("last_contacted"):
        reasons.append("No previous outreach detected")
    return reasons


def build_research_tasks(store, limit: int = None, selected_ids: list = None,
                         batch_type: str = "exploitation",
                         min_score: float = None) -> list:
    """Turn a diversified selection into one bounded research task each.

    batch_type: "exploitation" (acquisition, top-N) or "exploration"
    (discovery, unsegmented/least-known). The task records `stage` so
    downstream logic can compare outcomes by stage.
    """
    if batch_type not in BATCH_TYPES:
        batch_type = "exploitation"
    batch_type = _normalize_batch_type(batch_type)
    icp = load_icp()
    research = icp.get("research", {})
    limit = limit or int(research.get("top_n", 0))
    if limit <= 0:
        limit = None  # None = no cap, research all eligible
    quotas = research.get("quotas", {})
    default_quota = int(research.get("default_quota", 2))
    max_per_company = int(research.get("max_candidates_per_company", 3))
    budget = research.get("budget", {})

    if selected_ids:
        candidates = [store.get_prospect(pid) for pid in selected_ids]
        candidates = [c for c in candidates if c]
        selected, reasons = candidates, {c["prospect_id"]: _why_selected(c, c.get("segments", ["unsegmented"])[0])
                                         for c in candidates}
    else:
        if batch_type == "exploration":
            candidates = _eligible(store.prospects(limit=None))
        else:
            candidates = [c for c in _eligible(store.prospects(limit=None))
                          if c["prospect_id"] not in _researched_ids(store)]

        before = len(candidates)
        candidates = [c for c in candidates
                      if _pass_score_filter(c, min_score)]
        filtered = before - len(candidates)
        if filtered:
            print(f"[build_research_tasks] {filtered} prospects filtered "
                  f"(min_score={min_score}), {len(candidates)} remain")

        if batch_type == "exploration":
            selected, reasons = select_exploration_batch(
                candidates, size=limit, max_per_company=max_per_company,
                researched_ids=_researched_ids(store))
        else:
            selected, reasons = select_batch(
                candidates, size=limit, quotas=quotas, default_quota=default_quota,
                max_per_company=max_per_company)

    # Persist selection reasons on the prospect records.
    for p in selected:
        if p["prospect_id"] in reasons:
            p["selection_reasons"] = reasons[p["prospect_id"]]
            store.upsert_prospects([p])

    stage = "discovery" if batch_type == "exploration" else "acquisition"
    objective = ("determine_product_fit" if stage == "acquisition"
                 else "explore_unknown_fit")
    tasks = []
    for p in selected:
        tasks.append({
            "task_id": "research_" + uuid.uuid4().hex[:6],
            "prospect_id": p["prospect_id"],
            "stage": stage,
            "objective": objective,
            "required_fields": [
                "current_role", "company", "relevant_evidence",
                "potential_problem", "confidence",
            ],
            "budget": dict(budget) or {
                "max_steps": 12, "max_snapshots": 6,
                "max_pages": 1, "max_evidence": 8,
            },
            "status": "pending",
            "created_at": _now_iso(),
        })
    store.create_research_tasks(tasks)
    return tasks


def task_prompt(task: dict, prospect: dict) -> str:
    """The one-page research brief the browser agent receives (no DB dump)."""
    stage = task.get("stage", "acquisition")
    stage_note = (
        "STAGE: acquisition - confirm whether a plausible ICP candidate fits."
        if stage == "acquisition" else
        "STAGE: discovery - this candidate was NOT pre-classified as high-ICP. "
        "Do not assume fit; look for any evidence of the target problem, and "
        "say 'no evidence' honestly if there is none."
    )
    return f"""RESEARCH TASK {task['task_id']}
Prospect: {prospect.get('full_name') or prospect.get('name') or prospect['prospect_id']}
Profile URL: {prospect.get('linkedin_url')}
Objective: {task['objective']}
Required fields: {', '.join(task['required_fields'])}

{stage_note}

Budget: {task['budget']['max_steps']} browser steps,
    {task['budget']['max_snapshots']} snapshots,
    {task['budget']['max_pages']} profile page,
    {task['budget']['max_evidence']} evidence items.

STEPS:
1. browser_goto(url) to open the profile
2. browser_snapshot() to see the header
3. browser_scroll(dy=1000) to reach Experience section
4. browser_snapshot() to capture job history
5. browser_scroll(dy=1000) to reach About/Education
6. browser_snapshot() to capture remaining info
7. Extract all evidence and return JSON

Return a JSON object:
{{
  "findings": [
    {{"claim": "...", "evidence": "...", "source": "profile", "confidence": 0.9}}
  ],
  "summary": "..."
}}"""
