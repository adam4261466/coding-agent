"""Message validator: claims must be evidence-backed, personalization must be
real, duplicates rejected, length/style bounded."""

from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach.validator import validate_message

CAMPAIGN = {
    "campaign_id": "c1",
    "limits": {"min_evidence_confidence": 0.5, "contact_cooldown_days": 60},
    "target": {"segments": []},  # no segment restriction
}


def _prospect(store, **kw):
    p = {
        "prospect_id": "p1", "full_name": "Ada Lovelace", "first_name": "Ada",
        "raw_position": "AI Engineer at OpenAI", "current_company": "OpenAI",
        "status": "ready_for_outreach", "segments": [],
        "qualification_confidence": 0.9,
        "evidence": ["Role: AI Engineer at OpenAI", "Company: OpenAI"],
        "conversation_history": [],
    }
    p.update(kw)
    return p


def _msg(**kw):
    m = {
        "message_id": "m1", "prospect_id": "p1", "campaign_id": "c1",
        "strategy": "conversation_first", "version": "v1",
        "text": "Hi Ada, quick question about your work at OpenAI.",
        "claims": ["Ada is an AI Engineer at OpenAI"],
        "evidence_used": ["Role: AI Engineer at OpenAI"],
        "confidence": 0.9,
    }
    m.update(kw)
    return m


def test_supported_message_passes(tmp_path):
    store = Store(str(tmp_path / "v1.db"))
    try:
        out = validate_message(store, _prospect(store), CAMPAIGN, _msg())
        assert out["status"] == "approved", out["validation"]
    finally:
        store.close()


def test_unsupported_claim_rejected(tmp_path):
    store = Store(str(tmp_path / "v2.db"))
    try:
        msg = _msg(claims=["Ada is looking for a new database"],
                   text="Hi Ada, heard you need a new database?")
        out = validate_message(store, _prospect(store), CAMPAIGN, msg)
        assert out["status"] in ("needs_rework", "rejected")
        assert any("unsupported claim" in c for c in out["validation"])
    finally:
        store.close()


def test_fake_personalization_flagged(tmp_path):
    store = Store(str(tmp_path / "v3.db"))
    try:
        # No prior conversation, but message references one.
        msg = _msg(text="Hi Ada, loved our conversation about LLMs last week.")
        out = validate_message(store, _prospect(store), CAMPAIGN, msg)
        assert any("no prior conversation" in c for c in out["validation"])
    finally:
        store.close()


def test_real_personalization_backed(tmp_path):
    store = Store(str(tmp_path / "v4.db"))
    try:
        p = _prospect(store, conversation_history=["LLM fine-tuning setup"])
        msg = _msg(text="Hi Ada, enjoyed our chat about LLM fine-tuning.",
                   claims=[])
        out = validate_message(store, p, CAMPAIGN, msg)
        assert not any("no prior conversation" in c for c in out["validation"])
    finally:
        store.close()


def test_duplicate_message_rejected(tmp_path):
    store = Store(str(tmp_path / "v5.db"))
    try:
        first = _msg()
        store.save_message(first)
        dup = _msg(message_id="m2", text=first["text"])
        out = validate_message(store, _prospect(store), CAMPAIGN, dup)
        assert any("duplicate" in c for c in out["validation"])
    finally:
        store.close()


def test_too_long_rejected(tmp_path):
    store = Store(str(tmp_path / "v6.db"))
    try:
        long_text = ("Hi Ada, " + "details " * 130).strip()
        out = validate_message(store, _prospect(store), CAMPAIGN,
                               _msg(text=long_text, claims=[]))
        assert any("too long" in c for c in out["validation"])
    finally:
        store.close()


def test_empty_message_rejected(tmp_path):
    store = Store(str(tmp_path / "v7.db"))
    try:
        out = validate_message(store, _prospect(store), CAMPAIGN, _msg(text=""))
        assert any("empty" in c for c in out["validation"])
    finally:
        store.close()
