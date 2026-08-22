"""Attribution chain + Phase 3 analytics rates (deterministic counts)."""

from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach.attribution import funnel_for_campaign
from linkedin_intelligence.outreach.analytics import metrics_for, per_strategy


def _seed(store):
    store.upsert_prospects([
        {"prospect_id": "p1", "full_name": "A", "first_name": "A",
         "status": "ready_for_outreach", "segments": ["ai_ml_high_icp"],
         "qualification_confidence": 0.9},
        {"prospect_id": "p2", "full_name": "B", "first_name": "B",
         "status": "ready_for_outreach", "segments": ["ai_ml_high_icp"],
         "qualification_confidence": 0.9},
        {"prospect_id": "p3", "full_name": "C", "first_name": "C",
         "status": "ready_for_outreach", "segments": ["data_high_icp"],
         "qualification_confidence": 0.9},
    ])
    # p1: contacted, replied positively, visited, activated.
    store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                    "status": "ACTIVATED", "priority": 90,
                                    "assigned_strategy": "conversation_first"})
    # p2: contacted, no reply.
    store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p2",
                                    "status": "AWAITING_RESPONSE", "priority": 80,
                                    "assigned_strategy": "conversation_first"})
    # p3: assigned but never contacted.
    store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p3",
                                    "status": "CAMPAIGN_ASSIGNED", "priority": 70,
                                    "assigned_strategy": "problem_first"})
    store.save_conversation({"prospect_id": "p1", "campaign_id": "c1",
                             "intent": "interest", "sentiment": "positive",
                             "pain_signal": "present",
                             "commercial_intent": "high",
                             "objection": "unknown", "confidence": 0.9,
                             "raw": "{}"})
    store.record_product_event("p1", "visit", campaign_id="c1")
    store.record_product_event("p1", "signed_up", campaign_id="c1")
    store.record_product_event("p1", "activated", campaign_id="c1")
    store.record_product_event("p1", "customer", campaign_id="c1")


def test_funnel_for_campaign_counts(tmp_path):
    store = Store(str(tmp_path / "attr1.db"))
    try:
        _seed(store)
        f = funnel_for_campaign(store, "c1")
        assert f["contacted"] == 2  # only SENT+ states count
        assert f["visited"] == 1
        assert f["signed_up"] == 1
        assert f["activated"] == 1
        assert f["customer"] == 1
        assert f["activation_rate"] == 50.0
    finally:
        store.close()


def test_metrics_rates(tmp_path):
    store = Store(str(tmp_path / "attr2.db"))
    try:
        _seed(store)
        cps = store.campaign_prospects("c1")
        m = metrics_for(store, cps)
        assert m["contacted"] == 2
        assert m["replied"] == 1
        assert m["positive_replies"] == 1
        assert m["reply_rate"] == 50.0
        assert m["activation_rate"] == 50.0
    finally:
        store.close()


def test_per_strategy_breakdown(tmp_path):
    store = Store(str(tmp_path / "attr3.db"))
    try:
        _seed(store)
        by = per_strategy(store)
        assert by["conversation_first"]["contacted"] == 2
        assert by["problem_first"]["contacted"] == 0  # not yet sent
    finally:
        store.close()
