"""Calibration report: model decisions vs human-feedback ground truth."""

from linkedin_intelligence.calibration import build_calibration_report
from linkedin_intelligence.store import Store


def _seed(store):
    store.upsert_prospects([
        {"prospect_id": "p1", "full_name": "A", "segmentation_status": "segmented",
         "segments": ["data_high_icp"], "missing_signals": [],
         "total_score": 70, "role_category": "data", "company_type": "startup",
         "commercial_intent": "unknown", "conversation_count": 0,
         "relationship_score": 30, "engagement_score": 0,
         "problem_fit_score": None, "status": "ready_for_human_review"},
        {"prospect_id": "p2", "full_name": "B", "segmentation_status": "segmented",
         "segments": ["data_high_icp"], "missing_signals": [],
         "total_score": 66, "role_category": "data", "company_type": "startup",
         "commercial_intent": "unknown", "conversation_count": 0,
         "relationship_score": 30, "engagement_score": 0,
         "problem_fit_score": None, "status": "do_not_contact"},
        {"prospect_id": "p3", "full_name": "C", "segmentation_status": "unsegmented",
         "segments": ["unsegmented"], "missing_signals": ["minimum ICP score"],
         "total_score": 30, "role_category": "unknown", "company_type": "unknown",
         "commercial_intent": "unknown", "conversation_count": 0,
         "relationship_score": 30, "engagement_score": 0,
         "problem_fit_score": None, "status": "new"},
    ])
    # Model predicted READY for p1 and p2 (approvals); p3 not yet qualified.
    for pid, fit in (("p1", 80), ("p2", 78)):
        store.save_qualification({
            "prospect_id": pid, "fit_score": fit, "problem_fit_score": 70,
            "confidence": 0.9, "research_mode": "offline", "method": "llm",
            "reason": "r", "evidence_used": [], "uncertainties": [],
            "recommended_next_action": "READY_FOR_HUMAN_REVIEW",
            "contradictions": [], "research_stage": "acquisition",
            "model": "test"})
    # Human approves p1, rejects p2.
    store.record_feedback("p1", "ready_for_outreach", reason="real fit",
                          status_before="ready_for_human_review")
    store.record_feedback("p2", "do_not_contact", reason="wrong profile",
                          status_before="ready_for_human_review")


def test_calibration_metrics(tmp_path):
    store = Store(str(tmp_path / "c.db"))
    try:
        _seed(store)
        rep = build_calibration_report(store)
        assert rep["ground_truth"] == {"human_reviewed": 2, "approved": 1,
                                       "rejected": 1, "more_research_or_other": 0}
        m = rep["model_decision"]
        assert m["true_positives"] == 1
        assert m["false_positives"] == 1
        assert m["precision"] == 0.5
        assert m["recall"] == 1.0
        # Threshold sweep present and monotone in n_positive.
        assert len(rep["threshold_sweep"]) == 8
        assert rep["segmentation_coverage"]["unsegmented"] == 1
        assert rep["segmentation_coverage"]["unsegmented_pct"] == 33.3
        # Acquisition stage stats exist.
        assert rep["by_stage"]["acquisition"]["n"] == 2
    finally:
        store.close()


def test_calibration_without_feedback_is_not_an_error(tmp_path):
    store = Store(str(tmp_path / "d.db"))
    try:
        _seed(store)
        rep = build_calibration_report(store)
        assert rep["ground_truth"]["human_reviewed"] == 2
    finally:
        store.close()
