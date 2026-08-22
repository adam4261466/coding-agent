"""Reply classification: deterministic intent->state mapping, objection
taxonomy, and reply persistence (classification injected, no LLM needed)."""

from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach.conversation import next_state, record_reply
from linkedin_intelligence.outreach.objections import objection_category

CAMPAIGN = {"campaign_id": "c1", "name": "C1"}


def _prospect(store, pid="p1"):
    p = {
        "prospect_id": pid, "full_name": "Ada", "first_name": "Ada",
        "status": "ready_for_outreach", "segments": [],
        "qualification_confidence": 0.9,
    }
    store.upsert_prospects([p])
    return p


def test_intent_to_state_mapping():
    assert next_state({"intent": "opt_out"}) == "DO_NOT_CONTACT"
    assert next_state({"intent": "not_interested"}) == "NOT_INTERESTED"
    assert next_state({"intent": "interest"}) == "INTERESTED"
    assert next_state({"intent": "objection"}) == "CONVERSATION"
    assert next_state({"intent": "question"}) == "CONVERSATION"
    assert next_state({"intent": "unknown"}) == "CONVERSATION"


def test_objection_category_normalization():
    assert objection_category("Price") == "price"
    assert objection_category("already-have-solution") == "already_have_solution"
    assert objection_category("not understood") == "not_understood"
    assert objection_category("gibberish") == "unknown"


def test_record_reply_persists_and_moves_state(tmp_path):
    store = Store(str(tmp_path / "conv1.db"))
    try:
        store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                        "status": "SENT"})
        cls = {"intent": "interest", "sentiment": "positive",
               "pain_signal": "present", "commercial_intent": "high",
               "objection": "unknown", "confidence": 0.9, "summary": "wants demo"}
        record_reply(store, _prospect(store), CAMPAIGN, "yes, tell me more", cls)
        convs = store.conversations_outreach("p1")
        assert len(convs) == 1
        assert convs[0]["intent"] == "interest"
        assert store.get_campaign_prospect("c1", "p1")["status"] == "INTERESTED"
    finally:
        store.close()


def test_opt_out_reply_goes_to_dnc(tmp_path):
    store = Store(str(tmp_path / "conv2.db"))
    try:
        store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                        "status": "SENT"})
        cls = {"intent": "opt_out", "sentiment": "negative",
               "pain_signal": "absent", "commercial_intent": "none",
               "objection": "unknown", "confidence": 0.95, "summary": "please stop"}
        record_reply(store, _prospect(store), CAMPAIGN, "please stop contacting me", cls)
        assert store.get_campaign_prospect("c1", "p1")["status"] == "DO_NOT_CONTACT"
    finally:
        store.close()


def test_objection_reply_stays_in_conversation(tmp_path):
    store = Store(str(tmp_path / "conv3.db"))
    try:
        store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                        "status": "SENT"})
        cls = {"intent": "objection", "sentiment": "neutral",
               "pain_signal": "absent", "commercial_intent": "low",
               "objection": "price", "confidence": 0.85, "summary": "too expensive"}
        record_reply(store, _prospect(store), CAMPAIGN, "it's too expensive", cls)
        # Objections never auto-advance; the human responds.
        assert store.get_campaign_prospect("c1", "p1")["status"] == "CONVERSATION"
    finally:
        store.close()
