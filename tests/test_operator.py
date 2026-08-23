"""Permission-gated operator: queue buckets, deterministic planner,
permission gate, and the full observe -> connect -> message flow with a
fake browser. Confirms the agent obeys humans, not its own judgement."""

import json

import pytest

from linkedin_intelligence.store import Store
from linkedin_intelligence.agent.permissions import (
    allows, load_json, normalize)
from linkedin_intelligence.agent.queue import ProspectQueue
from linkedin_intelligence.agent.operator import AgentOperator
from linkedin_intelligence.outreach.campaign import (
    create_campaign, eligible_for_campaign, assign_prospects)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

CFG = {
    "_file": "t",
    "campaign": {"name": "T", "objective": "activated_signup"},
    "target": {"segments": ["ai_ml_high_icp"]},
    "strategy": {"primary": "conversation_first"},
    "limits": {"min_evidence_confidence": 0.5, "min_priority": 40,
               "max_new_prospects_per_batch": 5, "contact_cooldown_days": 60},
}


def _prospect(pid="p1", name="Ada Lovelace", score=85):
    return {
        "prospect_id": pid, "full_name": name, "first_name": name.split()[0],
        "raw_position": "AI Engineer at OpenAI",
        "current_company": "OpenAI",
        "linkedin_url": f"https://www.linkedin.com/in/{pid}",
        "connection_status": "unknown",
        "status": "ready_for_outreach", "segments": ["ai_ml_high_icp"],
        "qualification_fit": score, "qualification_confidence": 0.9,
        "pain_state": "plausible",
        "evidence": [f"Role: AI Engineer at OpenAI ({name})"],
        "conversation_history": [],
    }


class FakeBrowser:
    """Records every dispatched browser action."""

    def __init__(self):
        self.observes = []
        self.executes = []

    def observe(self, url, instruction, max_steps=12):
        self.observes.append(url)
        return {"success": True,
                "observations": [
                    {"claim": f"Role: AI Engineer at OpenAI ({url})",
                     "confidence": 0.95}],
                "summary": "profile looks active"}

    def execute(self, url, action, max_steps=10):
        self.executes.append((url, action[:60]))
        return {"success": True, "description": "done"}

    def close(self):
        pass


def _allow_all():
    return {"permission": "allowed",
            "flags": {"view_profile": True, "send_connection": True,
                      "send_message": True, "reply": True,
                      "follow_up": True}}


def _make_operator(tmp_path, browser=None):
    store = Store(str(tmp_path / "op.db"))
    op = AgentOperator(store, browser=browser or FakeBrowser(),
                       model="fake", base_url="http://fake")
    return store, op


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------

def test_permission_defaults_are_safe():
    rec = normalize(None)
    assert rec["permission"] == "manual"
    assert not any(rec[f] for f in
                   ("view_profile", "send_connection", "send_message",
                    "reply", "follow_up"))
    assert not allows(rec, "observe_profile")


def test_permission_store_roundtrip(tmp_path):
    store = Store(str(tmp_path / "perm.db"))
    try:
        assert store.get_permissions("x")["permission"] == "manual"
        store.set_permission("x", permission="allowed",
                             flags={"view_profile": True,
                                    "send_connection": True})
        rec = store.get_permissions("x")
        assert rec["permission"] == "allowed"
        assert rec["view_profile"] == 1 and rec["send_connection"] == 1
        assert rec["send_message"] == 0

        store.set_permission("x", flags={"send_message": True})
        assert store.get_permissions("x")["send_message"] == 1
        assert store.get_permissions("x")["view_profile"] == 1

        store.set_permission("x", permission="blocked")
        rec = store.get_permissions("x")
        assert rec["blocked"] == 1 and rec["permission"] == "blocked"

        with pytest.raises(ValueError):
            store.set_permission("x", permission="whatever")
        with pytest.raises(ValueError):
            store.set_permission("x", flags={"hack_the_planet": True})
    finally:
        store.close()


def test_blocked_overrides_flags():
    rec = normalize({"permission": "allowed", "send_message": 1,
                     "blocked": 1})
    assert not allows(rec, "send_message")


def test_manual_level_never_allows_even_with_flags():
    rec = normalize({"permission": "manual", "send_message": 1})
    assert not allows(rec, "send_message")


def test_load_json_accepts_aliases_and_rejects_garbage(tmp_path):
    path = tmp_path / "perms.json"
    path.write_text(json.dumps({
        "p1": {"permission": "allowed",
               "permissions": {"connect": True, "message": True}},
        "p2": {"permission": "blocked", "notes": "competitor"},
    }), encoding="utf-8")
    out = load_json(str(path))
    assert out["p1"]["send_connection"] is True
    assert out["p1"]["send_message"] is True
    assert out["p2"]["permission"] == "blocked"

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"p1": {"permission": "sure"}}),
                   encoding="utf-8")
    with pytest.raises(ValueError):
        load_json(str(bad))


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------

def test_queue_buckets_and_order(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    try:
        store.upsert_prospects([_prospect("low", "Low Score", score=40),
                                _prospect("high", "High Score", score=90),
                                _prospect("mid", "Mid Score", score=60)])
        store.set_permission("high", permission="allowed",
                             flags={"view_profile": True})
        store.set_permission("low", permission="blocked")
        # mid gets NO record -> safe default = manual review
        q = ProspectQueue(store)
        b = q.buckets()
        assert [e["prospect"]["prospect_id"] for e in b["allowed"]] == ["high"]
        assert [e["prospect"]["prospect_id"] for e in b["manual"]] == ["mid"]
        assert [e["prospect"]["prospect_id"] for e in b["blocked"]] == ["low"]
        assert q.next()["prospect"]["prospect_id"] == "high"
        assert q.summary() == {"allowed": 1, "manual": 1, "blocked": 1}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# planner + gate
# ---------------------------------------------------------------------------

def test_planner_is_deterministic_profile_first(tmp_path):
    store, op = _make_operator(tmp_path)
    try:
        store.upsert_prospects([_prospect()])
        entry = op.queue.allowed()[0] if op.queue.allowed() else None
        assert entry is None  # no permissions yet -> nobody is allowed

        store.set_permission("p1", **_allow_all())
        entry = op.queue.next()
        step = op.plan_step(entry["prospect"], entry["record"])
        assert step == {"action": "observe_profile"}

        op.memory.record_event("profile_observed", "p1")
        step = op.plan_step(entry["prospect"], entry["record"])
        assert step == {"action": "send_connection_request"}

        op.memory.record_event("connection_sent", "p1")
        op.memory.state.update_relationship("p1",
                                            connection_status="pending")
        ctx_pid = "p1"
        entry2 = op.queue.next()
        step = op.plan_step(entry2["prospect"], entry2["record"])
        assert step is None  # waiting for acceptance - nothing permitted now

        op.memory.record_event("connection_accepted", "p1")
        op.memory.state.update_relationship("p1",
                                            connection_status="connected")
        entry3 = op.queue.next()
        step = op.plan_step(entry3["prospect"], entry3["record"])
        assert step == {"action": "send_message"}
    finally:
        store.close()
        op.memory.close()


def test_gate_blocks_action_without_flag(tmp_path):
    store, op = _make_operator(tmp_path)
    try:
        store.upsert_prospects([_prospect()])
        store.set_permission("p1", permission="allowed",
                             flags={"view_profile": False})
        entry = op.queue.next()
        result = op.execute(entry["prospect"], entry["record"],
                            {"action": "observe_profile"})
        assert result["status"] == "blocked_by_permission"
        events = op.memory.events.recent_events("p1")
        assert events[-1]["event_type"] == "permission_blocked"
        assert op.browser.observes == []  # never touched LinkedIn
    finally:
        store.close()
        op.memory.close()


def test_dry_run_records_but_does_not_act(tmp_path):
    store, op = _make_operator(tmp_path)
    op.dry_run = True
    try:
        store.upsert_prospects([_prospect()])
        store.set_permission("p1", **_allow_all())
        result = op.run_once()
        assert result["status"] == "dry_run"
        assert result["action"] == "observe_profile"
        assert op.browser.observes == []
        types = [e["event_type"]
                 for e in op.memory.events.recent_events("p1")]
        assert "dry_run" in types
    finally:
        store.close()
        op.memory.close()


# ---------------------------------------------------------------------------
# full flow with a pre-approved message (no LLM needed)
# ---------------------------------------------------------------------------

def _assign_campaign(store):
    c = create_campaign(store, CFG)
    store.set_campaign_status(c["campaign_id"], "active")
    cands = eligible_for_campaign(store, c, store.prospects(limit=None))
    assign_prospects(store, c, cands)
    return c


def test_full_flow_observe_connect_send(tmp_path):
    store, op = _make_operator(tmp_path)
    try:
        store.upsert_prospects([_prospect()])
        store.set_permission("p1", **_allow_all())
        _assign_campaign(store)

        r1 = op.run_once()
        assert r1["action"] == "observe_profile"
        assert len(op.browser.observes) == 1

        r2 = op.run_once()
        assert r2["action"] == "send_connection_request"
        url, _ = op.browser.executes[-1]
        assert "p1" in url
        types = [e["event_type"]
                 for e in op.memory.events.recent_events("p1")]
        assert "connection_sent" in types

        # human pre-approves the exact message: operator sends it verbatim.
        # Seeding must follow the LEGAL state path the pipeline would take,
        # otherwise the state machine correctly refuses mark_sent().
        store.save_message({
            "message_id": "msg_seed1", "prospect_id": "p1",
            "campaign_id": r2.get("campaign_id") or "t",
            "text": "Hi Ada, quick question about your work at OpenAI?",
            "status": "approved_to_send", "approved": True,
            "version": "v1", "claims": [], "evidence_used": [],
            "validation": ["seeded"],
        })
        from linkedin_intelligence.outreach.state_machine import transition
        cp_row = store.get_campaign_prospect("t", "p1")
        for state, event in (("MESSAGE_GENERATED", "generated"),
                             ("MESSAGE_REVIEW", "validated"),
                             ("APPROVED_TO_SEND", "human_approved")):
            cp_row = transition(store, cp_row, state, event=event)

        # while the invitation is only PENDING, messaging stays locked
        r_wait = op.run_once()
        assert r_wait["status"] == "idle"
        assert len(op.browser.executes) == 1

        # the prospect accepts -> now the first message may go out
        op.memory.record_event("connection_accepted", "p1")
        op.memory.state.update_relationship("p1",
                                            connection_status="connected")
        r3 = op.run_once()
        assert r3["action"] == "send_message"
        assert r3["status"] == "sent"
        types = [e["event_type"]
                 for e in op.memory.events.recent_events("p1")]
        assert "message_sent" in types
        msgs = store.messages_for(prospect_id="p1")
        assert msgs[-1]["status"] == "approved_to_send"
        cp = store.get_campaign_prospect("t", "p1")
        assert cp["status"] == "AWAITING_RESPONSE"

        # everyone done -> idle, browser not touched again
        executes_before = len(op.browser.executes)
        r4 = op.run_once()
        assert r4["status"] == "idle"
        assert len(op.browser.executes) == executes_before
    finally:
        store.close()
        op.memory.close()


def test_failed_validation_routes_to_manual_review_not_browser(
        tmp_path, monkeypatch):
    store, op = _make_operator(tmp_path)
    try:
        import requests as _requests

        class BadResp:
            status_code = 200

            def json(self):
                return {"message": {"content": "not json at all"}}

        monkeypatch.setattr(_requests, "post",
                            lambda *a, **k: BadResp())

        store.upsert_prospects([_prospect()])
        store.set_permission("p1", **_allow_all())
        _assign_campaign(store)

        assert op.run_once()["action"] == "observe_profile"
        assert op.run_once()["action"] == "send_connection_request"
        op.memory.record_event("connection_accepted", "p1")
        op.memory.state.update_relationship("p1",
                                            connection_status="connected")

        result = op.run_once()
        assert result["status"] == "handed_off"
        events = op.memory.events.recent_events("p1")
        assert events[-1]["event_type"] == "handoff_requested"
        # only observe + connect ever hit the browser; no message was sent
        assert len(op.browser.executes) == 1
    finally:
        store.close()
        op.memory.close()


# ---------------------------------------------------------------------------
# failure handling
# ---------------------------------------------------------------------------

class BrokenBrowser(FakeBrowser):
    def observe(self, url, instruction, max_steps=12):
        self.observes.append(url)
        return {"success": False, "error": "agent aborted: Cannot connect "
                                           "to Ollama. Is it running?",
                "observations": []}


def test_failed_observation_is_not_treated_as_done(tmp_path):
    store, op = _make_operator(tmp_path)
    op.browser = BrokenBrowser()
    try:
        store.upsert_prospects([_prospect()])
        store.set_permission("p1", permission="allowed",
                             flags={"view_profile": True})
        entry = op.queue.next()
        result = op.execute(entry["prospect"], entry["record"],
                            {"action": "observe_profile"})
        assert result["status"] == "failed"
        # a failed observation must NOT satisfy the planner's profile step
        step = op.plan_step(entry["prospect"], entry["record"])
        assert step == {"action": "observe_profile"}
    finally:
        store.close()
        op.memory.close()


def test_repeated_failures_trip_circuit_breaker(tmp_path):
    store, op = _make_operator(tmp_path)
    op.browser = BrokenBrowser()
    try:
        store.upsert_prospects([_prospect()])
        store.set_permission("p1", **_allow_all())

        r1 = op.run_once()
        r2 = op.run_once()
        r3 = op.run_once()
        assert (r1["status"], r2["status"]) == ("failed", "failed")
        assert r3["status"] == "handed_off"
        types = [e["event_type"]
                 for e in op.memory.events.recent_events("p1")]
        assert "handoff_requested" in types

        # the person now waits for a human: no more browser attempts
        attempts_after_breaker = len(op.browser.observes)
        r4 = op.run_once()
        assert r4["status"] == "idle"
        assert len(op.browser.observes) == attempts_after_breaker
    finally:
        store.close()
        op.memory.close()


def test_human_cleared_handoff_resumes_work(tmp_path):
    store, op = _make_operator(tmp_path)
    op.browser = BrokenBrowser()
    try:
        store.upsert_prospects([_prospect()])
        store.set_permission("p1", **_allow_all())
        for _ in range(3):
            op.run_once()
        # human resolves the handoff
        op.memory.record_event("human_cleared", "p1", source="human")
        entry = op.queue.next()
        step = op.plan_step(entry["prospect"], entry["record"])
        assert step is not None  # planner picks the work up again
    finally:
        store.close()
        op.memory.close()
