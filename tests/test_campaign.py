"""Research batch selection is diversified: segment quotas + company caps."""

from linkedin_intelligence.research.campaign import (select_batch, _why_selected,
                                                     select_exploration_batch,
                                                     build_research_tasks)
from linkedin_intelligence.store import Store


def _c(pid, company, segments, score, role="software_engineering",
       status="new", segmentation_status="segmented", researched=True):
    return {
        "prospect_id": pid,
        "full_name": f"Person {pid}",
        "normalized_company": company,
        "segments": segments,
        "total_score": score,
        "role_category": role,
        "company_type": "startup",
        "conversation_count": 0,
        "last_contacted": None,
        "status": status,
        "segmentation_status": segmentation_status,
        "qualification_fit": 60 if researched else None,
    }


def test_company_cap_is_respected():
    candidates = [_c(f"a{i}", "acme", ["data_high_icp"], 80 - i) for i in range(6)]
    candidates += [_c(f"b{i}", "globex", ["data_high_icp"], 70 - i) for i in range(6)]
    selected, reasons = select_batch(candidates, size=8,
                                     quotas={"data_high_icp": 8},
                                     max_per_company=3)
    from collections import Counter
    counts = Counter(s["normalized_company"] for s in selected)
    assert counts["acme"] <= 3
    assert counts["globex"] <= 3
    # Cap 3/company over 2 companies => only 6 of the 8 slots are fillable.
    assert len(selected) == 6


def test_quota_segments_fill_before_overall_fill():
    a = _c("a1", "acme", ["leadership_high_icp"], 60)
    b = _c("b1", "globex", ["data_high_icp"], 90)
    selected, _ = select_batch(candidates=[a, b], size=2,
                               quotas={"leadership_high_icp": 1,
                                       "data_high_icp": 1},
                               default_quota=1, max_per_company=1)
    assert {s["prospect_id"] for s in selected} == {"a1", "b1"}


def test_every_selected_candidate_has_why_selected():
    p = _c("p1", "acme", ["data_high_icp"], 85)
    reasons = _why_selected(p, "data_high_icp")
    assert any("segment 'data_high_icp'" in r for r in reasons)
    assert any("Ordering score 85" in r for r in reasons)
    assert any("No previous outreach detected" in r for r in reasons)


def test_exploration_skips_already_researched():
    candidates = [
        _c("a1", "acme", ["data_high_icp"], 90, researched=True),
        _c("b1", "globex", ["data_high_icp"], 40, researched=False),
        _c("c1", "initech", ["data_high_icp"], 30, researched=False),
    ]
    selected, _ = select_exploration_batch(candidates, size=5,
                                           researched_ids={"a1"})
    assert "a1" not in {s["prospect_id"] for s in selected}


def test_exploration_prefers_unsegmented_and_respects_company_cap():
    candidates = [
        _c(f"x{i}", "acme", ["unsegmented"], 40 - i,
           segmentation_status="unsegmented", researched=False)
        for i in range(6)
    ]
    candidates += [
        _c(f"y{i}", "globex", ["data_high_icp"], 90,
           segmentation_status="segmented", researched=False)
        for i in range(4)
    ]
    selected, reasons = select_exploration_batch(candidates, size=8,
                                                 max_per_company=3)
    from collections import Counter
    counts = Counter(s["normalized_company"] for s in selected)
    assert counts["acme"] <= 3
    assert counts["globex"] <= 3
    assert len(selected) == 6
    for s in selected:
        assert any("Exploration batch (discovery)" in r
                   for r in reasons[s["prospect_id"]])


def test_build_tasks_record_stage(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    try:
        store.upsert_prospects([_c("p1", "acme", ["data_high_icp"], 85,
                                   researched=False)])
        tasks = build_research_tasks(store, limit=5, batch_type="exploration")
        assert tasks[0]["stage"] == "discovery"
        assert tasks[0]["objective"] == "explore_unknown_fit"
    finally:
        store.close()
