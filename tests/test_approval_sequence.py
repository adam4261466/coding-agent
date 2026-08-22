"""Approval queue + follow-up sequence (deterministic scheduling)."""

from datetime import datetime, timedelta, timezone

from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach.approval import approve, reject, approval_queue
from linkedin_intelligence.outreach.sequence import (
    follow_up_config, sent_follow_up_count, due_follow_ups, make_follow_up_eligible,
)

CAMPAIGN = {
    "campaign_id": "c1",
    "sequence": {"follow_up_days": [3, 7], "max_follow_ups": 2},
}


def _msg(store, mid="m1", pid="p1"):
    store.save_message({
        "message_id": mid, "prospect_id": pid, "campaign_id": "c1",
        "strategy": "conversation_first", "version": "v1",
        "text": "Hi Ada, question about OpenAI?",
        "claims": [], "evidence_used": [], "confidence": 0.9,
        "approved": False, "validation": ["ok"], "status": "approved",
    })
    return store.get_message(mid)


def test_approve_moves_message_and_state(tmp_path):
    store = Store(str(tmp_path / "ap1.db"))
    try:
        store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                        "status": "MESSAGE_REVIEW", "priority": 50})
        _msg(store)
        out = approve(store, "m1")
        assert out["approved"] is True
        assert out["status"] == "approved_to_send"
        assert store.get_campaign_prospect("c1", "p1")["status"] == "APPROVED_TO_SEND"
    finally:
        store.close()


def test_reject_leaves_message_rejected(tmp_path):
    store = Store(str(tmp_path / "ap2.db"))
    try:
        store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                        "status": "MESSAGE_REVIEW", "priority": 50})
        _msg(store)
        reject(store, "m1", note="rewrite")
        assert store.get_message("m1")["status"] == "rejected"
    finally:
        store.close()


def test_approval_queue_only_approved_status(tmp_path):
    store = Store(str(tmp_path / "ap3.db"))
    try:
        store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                        "status": "MESSAGE_REVIEW", "priority": 50})
        _msg(store)
        store.save_message({
            "message_id": "m2", "prospect_id": "p2", "campaign_id": "c1",
            "strategy": "problem_first", "version": "v1", "text": "other",
            "claims": [], "evidence_used": [], "confidence": 0.5,
            "approved": False, "validation": [], "status": "needs_rework",
        })
        queue = approval_queue(store)
        assert [m["message_id"] for m in queue] == ["m1"]
    finally:
        store.close()


def test_follow_up_due_after_cooldown(tmp_path):
    store = Store(str(tmp_path / "ap4.db"))
    try:
        cfg = follow_up_config(CAMPAIGN)
        assert cfg == {"days": [3, 7], "max_follow_ups": 2}

        store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                        "status": "AWAITING_RESPONSE", "priority": 50})
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        store.log_outreach_event("p1", "c1", "APPROVED_TO_SEND", "SENT",
                                 "sent", "first outbound")
        # Force the sent timestamp into the past by rewriting it.
        store.conn.execute("UPDATE outreach_events SET created_at = ? "
                           "WHERE prospect_id = 'p1' AND event = 'sent'", (old,))
        store.conn.commit()

        due = due_follow_ups(store, CAMPAIGN)
        assert len(due) == 1
        moved = make_follow_up_eligible(store, CAMPAIGN)
        assert len(moved) == 1
        assert moved[0]["status"] == "FOLLOWUP_ELIGIBLE"
    finally:
        store.close()


def test_follow_up_budget_exhausted(tmp_path):
    store = Store(str(tmp_path / "ap5.db"))
    try:
        store.assign_campaign_prospect({"campaign_id": "c1", "prospect_id": "p1",
                                        "status": "AWAITING_RESPONSE", "priority": 50})
        store.log_outreach_event("p1", "c1", "APPROVED_TO_SEND", "SENT",
                                 "sent", "1st")
        store.log_outreach_event("p1", "c1", "FOLLOWUP_ELIGIBLE", "MESSAGE_PENDING",
                                 "follow_up_sent", "2nd")
        store.log_outreach_event("p1", "c1", "FOLLOWUP_ELIGIBLE", "MESSAGE_PENDING",
                                 "follow_up_sent", "3rd")
        # Even with a past timestamp the budget (2) is exhausted.
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        store.conn.execute("UPDATE outreach_events SET created_at = ?",
                           (old,))
        store.conn.commit()
        assert sent_follow_up_count(store, "p1", "c1") >= 2
        assert due_follow_ups(store, CAMPAIGN) == []
    finally:
        store.close()
