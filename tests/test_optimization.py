"""Phase 4 optimization engine: statistics, funnel, hypotheses, experiments,
recommendations, ICP learning."""

from linkedin_intelligence.store import Store
from linkedin_intelligence.optimization.evaluator import wilson_ci, two_proportion_test
from linkedin_intelligence.optimization.funnel_analysis import funnel_analysis
from linkedin_intelligence.optimization.hypothesis import generate_hypotheses
from linkedin_intelligence.optimization.experiment import (
    create_experiment, evaluate_experiment, summarize)
from linkedin_intelligence.optimization.recommendations import (
    recommendations, icp_learning_report)
from linkedin_intelligence.optimization.calibration import (
    build_optimization_calibration)


def test_wilson_ci_edges():
    assert wilson_ci(0, 0)["rate"] == 0.0
    assert wilson_ci(0, 10)["rate"] == 0.0
    assert wilson_ci(5, 10)["rate"] == 0.5
    assert 0.0 < wilson_ci(10, 10)["ci_low"] < 1.0


def test_no_winner_without_min_sample():
    # 12/100 (12%) vs 2/10 (20%): the small sample must NOT win.
    r = two_proportion_test(2, 10, 12, 100, min_effect=0.05)
    assert r["verdict"] == "insufficient_data"
    assert "minimum" in r["reason"]


def test_winner_only_with_significant_effect():
    r = two_proportion_test(30, 50, 12, 50, min_effect=0.05)
    assert r["verdict"] == "winner"
    assert r["z"] > 1.96


def test_identical_rates_not_a_winner():
    r = two_proportion_test(10, 40, 10, 40, min_effect=0.05)
    assert r["verdict"] in ("no_significant_difference", "winner")


def _seed_funnel(store):
    # 4 prospects: 2 activated in ai segment via conversation_first, 1 no
    # reply, 1 not contacted.
    store.save_campaign({"campaign_id": "c1", "name": "c1",
                         "objective": "activated_signup",
                         "strategy": "conversation_first", "status": "running",
                         "config": {}})
    for pid, seg in (("p1", "ai_ml_high_icp"), ("p2", "ai_ml_high_icp"),
                     ("p3", "ai_ml_high_icp"), ("p4", "data_high_icp")):
        store.upsert_prospects([{
            "prospect_id": pid, "full_name": pid, "first_name": pid,
            "status": "ready_for_human_review", "segments": [seg],
            "qualification_confidence": 0.9,
        }])
    store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                    "status": "ACTIVATED", "priority": 1,
                                    "assigned_strategy": "conversation_first"})
    store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p2",
                                    "status": "ACTIVATED", "priority": 2,
                                    "assigned_strategy": "conversation_first"})
    store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p3",
                                    "status": "AWAITING_RESPONSE", "priority": 3,
                                    "assigned_strategy": "conversation_first"})
    store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p4",
                                    "status": "CAMPAIGN_ASSIGNED", "priority": 4,
                                    "assigned_strategy": "problem_first"})
    for pid in ("p1", "p2"):
        store.record_product_event(pid, "visit", campaign_id="c1")
        store.record_product_event(pid, "signup_started", campaign_id="c1")
        store.record_product_event(pid, "signed_up", campaign_id="c1")
        store.record_product_event(pid, "activated", campaign_id="c1")


def test_funnel_bottleneck_detected(tmp_path):
    store = Store(str(tmp_path / "opt1.db"))
    try:
        _seed_funnel(store)
        f = funnel_analysis(store)
        assert f["totals"]["contacted"] == 3
        assert f["totals"]["activated"] == 2
        # activated->customer is a 100% drop (0/2): the bottleneck.
        assert f["bottleneck"]["to"] == "customer"
    finally:
        store.close()


def test_experiment_lifecycle(tmp_path):
    store = Store(str(tmp_path / "opt2.db"))
    try:
        _seed_funnel(store)
        exp = create_experiment(store, "h", control="problem_first",
                                variant="conversation_first")
        out = evaluate_experiment(store, exp["experiment_id"])
        assert out["status"] in ("running", "concluded")
        assert out["result"]["verdict"] in (
            "winner", "loser", "no_significant_difference", "insufficient_data")
        assert summarize(store)[0]["experiment_id"] == exp["experiment_id"]
    finally:
        store.close()


def test_hypotheses_data_driven(tmp_path):
    store = Store(str(tmp_path / "opt3.db"))
    try:
        _seed_funnel(store)
        hs = generate_hypotheses(store)
        assert all(h["status"] == "suggested" for h in hs)
        assert all(h.get("hypothesis_id") for h in hs)
    finally:
        store.close()


def test_recommendations_require_approval(tmp_path):
    store = Store(str(tmp_path / "opt4.db"))
    try:
        _seed_funnel(store)
        recs = recommendations(store)
        for r in recs:
            assert r["requires_approval"] is True
            assert r["status"] == "proposed"
    finally:
        store.close()


def test_icp_learning_report_pending_decision(tmp_path):
    store = Store(str(tmp_path / "opt5.db"))
    try:
        _seed_funnel(store)
        rep = icp_learning_report(store)
        assert rep["funnel"]["totals"]["activated"] == 2
        # No automatic ICP change: decision is pending human accept/reject.
        assert "accept" in rep["recommendation"]
    finally:
        store.close()


def test_optimization_calibration_counts(tmp_path):
    store = Store(str(tmp_path / "opt6.db"))
    try:
        _seed_funnel(store)
        store.record_feedback("p1", "ready_for_outreach",
                              status_before="ready_for_human_review")
        store.record_feedback("p3", "ready_for_outreach",
                              status_before="ready_for_human_review")
        c = build_optimization_calibration(store)
        assert c["contacted"] == 3
        assert c["human_approved"] == 2
        assert c["true_positives"] == 1  # p1 approved + activated
        assert c["false_positives"] == 1  # p3 approved + no reply
        assert c["precision"] == 0.5
    finally:
        store.close()
