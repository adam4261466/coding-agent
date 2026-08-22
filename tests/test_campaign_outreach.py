"""Campaign lifecycle: create, assign eligible prospects by priority, start
the next message batch. Deterministic ordering, no LLM."""

from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach.campaign import (
    create_campaign, eligible_for_campaign, assign_prospects, next_batch,
    assign_batch,
)

CFG = {
    "_file": "ai_engineers",
    "campaign": {"name": "AI Engineers", "objective": "activated_signup"},
    "target": {"segments": ["ai_ml_high_icp"]},
    "strategy": {"primary": "conversation_first"},
    "limits": {"min_evidence_confidence": 0.5, "min_priority": 40,
               "max_new_prospects_per_batch": 2, "contact_cooldown_days": 60},
}


def _prospect(pid, fit=80, conf=0.9, pain=None, segments=("ai_ml_high_icp",),
              **kw):
    p = {
        "prospect_id": pid, "full_name": f"P{pid}", "first_name": f"P{pid}",
        "status": "ready_for_outreach", "segments": list(segments),
        "qualification_fit": fit, "qualification_confidence": conf,
        "pain_state": pain, "total_score": fit, "last_contacted": None,
    }
    p.update(kw)
    return p


def test_create_campaign_from_config(tmp_path):
    store = Store(str(tmp_path / "cp1.db"))
    try:
        c = create_campaign(store, CFG)
        assert c["campaign_id"] == "ai_engineers"
        assert c["objective"] == "activated_signup"
        assert c["strategy"] == "conversation_first"
        assert store.get_campaign("ai_engineers")["campaign_id"] == "ai_engineers"
    finally:
        store.close()


def test_eligible_for_campaign_ranks_by_priority(tmp_path):
    store = Store(str(tmp_path / "cp2.db"))
    try:
        store.upsert_prospects([
            _prospect("a", fit=50, pain="demonstrated"),
            _prospect("b", fit=95, conf=0.5),
            _prospect("c", fit=30, conf=0.1),
        ])
        c = create_campaign(store, CFG)
        ranked = eligible_for_campaign(store, c, store.prospects(limit=None))
        assert [p["prospect_id"] for p in ranked] == ["b", "a"]  # c below min
        assert ranked[0]["outreach_priority"] > ranked[1]["outreach_priority"]
    finally:
        store.close()


def test_assign_then_next_batch(tmp_path):
    store = Store(str(tmp_path / "cp3.db"))
    try:
        store.upsert_prospects([
            _prospect("a", fit=80), _prospect("b", fit=70),
        ])
        c = create_campaign(store, CFG)
        assigned = assign_prospects(store, c, store.prospects(limit=None))
        assert len(assigned) == 2
        cps = store.campaign_prospects("ai_engineers")
        assert all(x["status"] == "CAMPAIGN_ASSIGNED" for x in cps)
        batch = next_batch(store, c, limit=1)
        assert len(batch) == 1
        assert batch[0]["prospect_id"] == "a"  # highest priority first
        remaining = store.campaign_prospects("ai_engineers", status="CAMPAIGN_ASSIGNED")
        assert len(remaining) == 1
    finally:
        store.close()


def test_assign_batch_limits_and_summary(tmp_path):
    store = Store(str(tmp_path / "cp4.db"))
    try:
        store.upsert_prospects([_prospect(f"p{i}", fit=90) for i in range(5)])
        c = create_campaign(store, CFG)
        summary = assign_batch(store, c, store.prospects(limit=None))
        assert summary["assigned"] == 2  # max_new_prospects_per_batch
        assert summary["started"] == 2
    finally:
        store.close()


def test_same_prospect_not_reassigned(tmp_path):
    store = Store(str(tmp_path / "cp5.db"))
    try:
        store.upsert_prospects([_prospect("a", fit=85)])
        c = create_campaign(store, CFG)
        assign_prospects(store, c, store.prospects(limit=None))
        again = eligible_for_campaign(store, c, store.prospects(limit=None))
        assert again == []
    finally:
        store.close()
