"""Deterministic eligibility - every check is a rule, never the LLM."""

from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach.eligibility import is_eligible, eligible_prospects

CAMPAIGN = {
    "campaign_id": "c1",
    "limits": {"min_evidence_confidence": 0.5, "contact_cooldown_days": 60},
    "target": {"segments": ["ai_ml_high_icp"]},
}


def _prospect(**kw):
    p = {
        "prospect_id": "p1", "full_name": "Ada",
        "status": "ready_for_outreach", "segments": ["ai_ml_high_icp"],
        "qualification_confidence": 0.9, "last_contacted": None,
        "commercial_intent": "unknown",
    }
    p.update(kw)
    return p


def test_fully_eligible_passes(tmp_path):
    store = Store(str(tmp_path / "el1.db"))
    try:
        ok, checks = is_eligible(store, _prospect(), CAMPAIGN)
        assert ok, checks
        assert len(checks) == 7
    finally:
        store.close()


def test_needs_human_approval(tmp_path):
    store = Store(str(tmp_path / "el2.db"))
    try:
        ok, checks = is_eligible(store, _prospect(status="pending_review"), CAMPAIGN)
        assert not ok
        assert not {r: ok for ok, r in checks}["human_approved"]
    finally:
        store.close()


def test_do_not_contact_blocks(tmp_path):
    store = Store(str(tmp_path / "el3.db"))
    try:
        ok, checks = is_eligible(store, _prospect(status="do_not_contact"), CAMPAIGN)
        assert not ok
    finally:
        store.close()


def test_exclusion_blocks(tmp_path):
    store = Store(str(tmp_path / "el4.db"))
    try:
        store.save_exclusions([{"prospect_id": "p1", "name": "X",
                                "reasons": ["already a customer"]}])
        ok, checks = is_eligible(store, _prospect(), CAMPAIGN)
        assert not ok
        assert not {r: ok for ok, r in checks}["no_exclusion"]
    finally:
        store.close()


def test_recently_contacted_blocks(tmp_path):
    from datetime import datetime, timezone, timedelta
    store = Store(str(tmp_path / "el5.db"))
    try:
        last = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        ok, checks = is_eligible(store, _prospect(last_contacted=last), CAMPAIGN)
        assert not ok
        assert not {r: ok for ok, r in checks}["not_recently_contacted"]
    finally:
        store.close()


def test_active_conversation_blocks(tmp_path):
    store = Store(str(tmp_path / "el6.db"))
    try:
        store.assign_campaign_prospect({"campaign_id": "other",
                                        "prospect_id": "p1",
                                        "status": "SENT"})
        ok, checks = is_eligible(store, _prospect(), CAMPAIGN)
        assert not ok
        assert not {r: ok for ok, r in checks}["no_active_conversation"]
    finally:
        store.close()


def test_segment_mismatch_blocks(tmp_path):
    store = Store(str(tmp_path / "el7.db"))
    try:
        ok, checks = is_eligible(store, _prospect(segments=["data_high_icp"]), CAMPAIGN)
        assert not ok
        assert not {r: ok for ok, r in checks}["campaign_segment_match"]
    finally:
        store.close()


def test_confidence_below_threshold_blocks(tmp_path):
    store = Store(str(tmp_path / "el8.db"))
    try:
        ok, checks = is_eligible(store, _prospect(qualification_confidence=0.2),
                                 CAMPAIGN)
        assert not ok
        assert not {r: ok for ok, r in checks}["evidence_confidence_threshold"]
    finally:
        store.close()


def test_eligible_prospects_filters(tmp_path):
    store = Store(str(tmp_path / "el9.db"))
    try:
        ps = [
            _prospect(prospect_id="good"),
            _prospect(prospect_id="bad", status="do_not_contact"),
        ]
        got = eligible_prospects(store, CAMPAIGN, ps)
        assert [p["prospect_id"] for p in got] == ["good"]
    finally:
        store.close()
