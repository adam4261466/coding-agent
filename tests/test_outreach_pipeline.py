"""End-to-end outreach pipeline with a mocked LLM: generate -> validate ->
state transitions -> approval. Confirms the wiring, not the model."""

import json

from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach.pipeline import produce_message, produce_batch
from linkedin_intelligence.outreach.approval import approval_queue
from linkedin_intelligence.outreach.campaign import (
    create_campaign, eligible_for_campaign, assign_prospects, next_batch)

CFG = {
    "_file": "t",
    "campaign": {"name": "T", "objective": "activated_signup"},
    "target": {"segments": ["ai_ml_high_icp"]},
    "strategy": {"primary": "conversation_first"},
    "limits": {"min_evidence_confidence": 0.5, "min_priority": 40,
               "max_new_prospects_per_batch": 5, "contact_cooldown_days": 60},
}


def _prospect(pid="p1"):
    return {
        "prospect_id": pid, "full_name": "Ada Lovelace", "first_name": "Ada",
        "raw_position": "AI Engineer at OpenAI", "current_company": "OpenAI",
        "status": "ready_for_outreach", "segments": ["ai_ml_high_icp"],
        "qualification_fit": 85, "qualification_confidence": 0.9,
        "pain_state": "plausible", "evidence": ["Role: AI Engineer at OpenAI"],
        "conversation_history": [],
    }


def _fake_llm(monkeypatch):
    """Stand-in Ollama /api/chat returning a valid structured message."""
    import requests
    class FakeResp:
        status_code = 200
        def json(self):
            return {"message": {"content": json.dumps({
                "strategy": "Conversation First",
                "message": "Hi Ada, question about your work at OpenAI?",
                "evidence_used": ["Role: AI Engineer at OpenAI"],
                "claims": ["Ada is an AI Engineer at OpenAI"],
                "confidence": 0.8,
            })}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp())


def test_pipeline_generates_validates_and_queues(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "pp1.db"))
    try:
        _fake_llm(monkeypatch)
        store.upsert_prospects([_prospect()])
        c = create_campaign(store, CFG)
        cands = eligible_for_campaign(store, c, store.prospects(limit=None))
        assign_prospects(store, c, cands)
        batch = next_batch(store, c, limit=1)
        msg = produce_message(store, c, batch[0], model="fake")
        assert msg["status"] in ("approved", "needs_rework", "rejected")
        assert msg["text"].startswith("Hi Ada")
        assert msg["version"] == "v1"
        # Message persisted, state advanced, approved message in queue.
        assert store.get_message(msg["message_id"]) is not None
        cp = store.get_campaign_prospect("t", "p1")
        assert cp["status"] == "MESSAGE_REVIEW"
        queue = approval_queue(store, "t")
        assert any(m["message_id"] == msg["message_id"] for m in queue)
    finally:
        store.close()


def test_regeneration_creates_immutable_versions(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "pp2.db"))
    try:
        _fake_llm(monkeypatch)
        store.upsert_prospects([_prospect()])
        c = create_campaign(store, CFG)
        assign_prospects(store, c, eligible_for_campaign(store, c,
                                                         store.prospects(limit=None)))
        cp = next_batch(store, c, limit=1)[0]
        v1 = produce_message(store, c, cp, model="fake")
        v2 = produce_message(store, c, cp, model="fake")
        assert v1["version"] == "v1"
        assert v2["version"] == "v2"
        assert v1["message_id"] != v2["message_id"]
        # v1 still intact after the regeneration.
        assert store.get_message(v1["message_id"])["text"] == v1["text"]
        # Two different versions, same prospect/campaign.
        msgs = store.messages_for(prospect_id="p1", campaign_id="t")
        assert {m["version"] for m in msgs} == {"v1", "v2"}
    finally:
        store.close()
