"""Tests for the 4-layer memory system and agent modules."""

import json
import pytest

from linkedin_intelligence.store import Store
from linkedin_intelligence.agent.orchestrator import AgentOrchestrator
from linkedin_intelligence.agent.planner import (
    plan_next_action, _parse_plan, _fallback_plan)
from linkedin_intelligence.agent.executor import execute_plan
from linkedin_intelligence.automation.memory.memory_service import MemoryService
from linkedin_intelligence.automation.memory.event_store import EventStore
from linkedin_intelligence.automation.memory.state_store import StateStore
from linkedin_intelligence.automation.memory.fact_store import FactStore
from linkedin_intelligence.automation.memory.task_store import TaskStore
from linkedin_intelligence.automation.memory.snapshot_store import SnapshotStore
from linkedin_intelligence.automation.monitoring.eligibility import (
    sweep_follow_ups, sweep_conversations, sweep_message_pending, sweep_all)
from linkedin_intelligence.automation.monitoring.handoff import (
    handoff_needed, check_approval_required)
from linkedin_intelligence.automation.actions.linkedin_actions import ACTION_MAP
from linkedin_intelligence.outreach.state_machine import transition


# ---- helpers ----

def _prospect(pid="p1", name="Alice Smith", company="Acme Corp",
              position="AI Engineer", score=50.0, connected=True,
              segments=None):
    return {
        "prospect_id": pid, "full_name": name,
        "first_name": name.split()[0], "last_name": name.split()[-1],
        "linkedin_url": f"https://linkedin.com/in/{pid}",
        "current_company": company, "normalized_company": company.lower(),
        "raw_position": position, "normalized_position": position.lower(),
        "role_category": "ai_ml", "total_score": score,
        "relationship_score": 30.0,
        "connection_status": "connected" if connected else "not_connected",
        "segments": segments or ["ai_ml_high_icp"],
        "status": "ready_for_outreach",
    }


def _campaign(cid="c1", name="Test Campaign", status="active"):
    return {
        "campaign_id": cid, "name": name, "objective": "activated_signup",
        "strategy": "conversation_first", "status": status,
        "approval": {"required": True},
        "config": {"target": {"segments": ["ai_ml_high_icp"]},
                   "limits": {"max_new_prospects_per_batch": 10,
                              "contact_cooldown_days": 60},
                   "sequence": {"follow_up_days": [3, 7, 14],
                                "max_follow_ups": 2},
                   "approval": {"required": True}},
    }


# ---- EventStore tests ----

class TestEventStore:
    def test_append_and_retrieve(self, tmp_path):
        store = EventStore(str(tmp_path / "ev.db"))
        try:
            store.append("test_event", prospect_id="p1", data={"foo": "bar"})
            events = store.events_for(prospect_id="p1")
            assert len(events) == 1
            assert events[0]["event_type"] == "test_event"
            assert events[0]["data"]["foo"] == "bar"
        finally:
            store.close()

    def test_recent_events_chronological(self, tmp_path):
        store = EventStore(str(tmp_path / "ev2.db"))
        try:
            store.append("event_a", prospect_id="p1")
            store.append("event_b", prospect_id="p1")
            store.append("event_c", prospect_id="p1")
            recent = store.recent_events("p1", n=2)
            assert len(recent) == 2
            assert recent[0]["event_type"] == "event_b"
            assert recent[1]["event_type"] == "event_c"
        finally:
            store.close()

    def test_last_event_of_type(self, tmp_path):
        store = EventStore(str(tmp_path / "ev3.db"))
        try:
            store.append("connection_sent", prospect_id="p1")
            store.append("message_sent", prospect_id="p1")
            store.append("connection_sent", prospect_id="p1")
            last = store.last_event_of_type("p1", "connection_sent")
            assert last["id"] > 1  # second one
        finally:
            store.close()


# ---- StateStore tests ----

class TestStateStore:
    def test_empty_state(self, tmp_path):
        store = StateStore(str(tmp_path / "st.db"))
        try:
            state = store.get_state("p1")
            assert state["identity"]["name"] is None
            assert state["product"]["signed_up"] is False
        finally:
            store.close()

    def test_update_and_get(self, tmp_path):
        store = StateStore(str(tmp_path / "st2.db"))
        try:
            store.update_identity("p1", name="Alice", company="Acme")
            state = store.get_state("p1")
            assert state["identity"]["name"] == "Alice"
            assert state["identity"]["company"] == "Acme"
        finally:
            store.close()

    def test_reconcile(self, tmp_path):
        store = StateStore(str(tmp_path / "st3.db"))
        try:
            store.update_product("p1", signed_up=False)
            result = store.reconcile("p1", "product", "signed_up",
                                     True, "app_database", 1.0)
            assert result is not None
            assert result["old_value"] is False
            assert result["new_value"] is True
            state = store.get_state("p1")
            assert state["product"]["signed_up"] is True
        finally:
            store.close()

    def test_reconcile_no_change(self, tmp_path):
        store = StateStore(str(tmp_path / "st4.db"))
        try:
            store.update_product("p1", signed_up=True)
            result = store.reconcile("p1", "product", "signed_up",
                                     True, "app_database")
            assert result is None
        finally:
            store.close()


# ---- FactStore tests ----

class TestFactStore:
    def test_add_and_get(self, tmp_path):
        store = FactStore(str(tmp_path / "fc.db"))
        try:
            store.add_fact("p1", "uses_technology", "Python",
                           confidence=0.95, source="linkedin_observation")
            facts = store.get_facts("p1")
            assert len(facts) == 1
            assert facts[0]["predicate"] == "uses_technology"
            assert facts[0]["object"] == "Python"
        finally:
            store.close()

    def test_supersede_old_fact(self, tmp_path):
        store = FactStore(str(tmp_path / "fc2.db"))
        try:
            store.add_fact("p1", "company", "Acme v1")
            store.add_fact("p1", "company", "Acme v2")
            active = store.get_facts("p1", predicate="company")
            assert len(active) == 1
            assert active[0]["object"] == "Acme v2"
            history = store.fact_history("p1", "company")
            assert len(history) == 2
        finally:
            store.close()

    def test_source_trust(self, tmp_path):
        store = FactStore(str(tmp_path / "fc3.db"))
        try:
            f1 = store.add_fact("p1", "x", "a", source="app_database")
            f2 = store.add_fact("p1", "x", "b", source="llm_speculation")
            assert f1["confidence"] > f2["confidence"]
        finally:
            store.close()

    def test_fact_summary(self, tmp_path):
        store = FactStore(str(tmp_path / "fc4.db"))
        try:
            store.add_fact("p1", "uses", "Python", source="linkedin_observation")
            store.add_fact("p1", "company", "Acme", source="linkedin_observation")
            summary = store.fact_summary("p1")
            assert "uses" in summary
            assert "company" in summary
            assert len(summary) == 2
        finally:
            store.close()


# ---- TaskStore tests ----

class TestTaskStore:
    def test_create_and_progress(self, tmp_path):
        store = TaskStore(str(tmp_path / "tk.db"))
        try:
            task = store.create_task("p1", "convert_prospect",
                                     initial_steps=["research", "message",
                                                    "follow_up"])
            assert task["status"] == "active"
            assert len(task["pending"]) == 3
            store.complete_step(task["task_id"], "research")
            progress = store.progress(task["task_id"])
            assert progress["completed"] == 1
            assert progress["pending"] == 2
        finally:
            store.close()

    def test_crash_recovery(self, tmp_path):
        store = TaskStore(str(tmp_path / "tk2.db"))
        try:
            store.create_task("p1", "convert", initial_steps=["a", "b", "c"])
            store.create_task("p2", "activate", initial_steps=["x", "y"])
            active = store.active_tasks()
            assert len(active) == 2
            pids = {t["prospect_id"] for t in active}
            assert "p1" in pids and "p2" in pids
        finally:
            store.close()


# ---- SnapshotStore tests ----

class TestSnapshotStore:
    def test_save_and_recover(self, tmp_path):
        store = SnapshotStore(str(tmp_path / "sn.db"))
        try:
            state = {"identity": {"name": "Alice"}}
            task = {"goal": "convert"}
            store.save_snapshot("p1", state, task)
            latest = store.latest_snapshot("p1")
            assert latest["state"]["identity"]["name"] == "Alice"
            assert latest["task"]["goal"] == "convert"
        finally:
            store.close()


# ---- MemoryService tests ----

class TestMemoryService:
    def test_full_context(self, tmp_path):
        mem = MemoryService(str(tmp_path / "mem.db"))
        try:
            p = _prospect()
            mem.init_from_store(None, p)
            mem.record_fact("p1", "uses", "Python",
                            source="linkedin_observation")
            mem.record_event("profile_observed", "p1", data={"company": "NewCorp"})
            ctx = mem.get_prospect_context("p1")
            assert ctx["state"]["identity"]["name"] == "Alice Smith"
            assert ctx["state"]["identity"]["company"] == "NewCorp"
            assert "uses" in ctx["facts"]
        finally:
            mem.close()

    def test_reconcile_records_event(self, tmp_path):
        mem = MemoryService(str(tmp_path / "mem2.db"))
        try:
            p = _prospect()
            mem.init_from_store(None, p)
            mem.reconcile_state("p1", "product", "signed_up", True,
                                "app_database", 1.0)
            events = mem.events.events_for(prospect_id="p1",
                                           event_type="state_reconciliation")
            assert len(events) == 1
        finally:
            mem.close()

    def test_snapshot_and_recover(self, tmp_path):
        mem = MemoryService(str(tmp_path / "mem3.db"))
        try:
            p = _prospect()
            mem.init_from_store(None, p)
            mem.start_task("p1", "convert_prospect", steps=["a", "b"])
            mem.save_snapshot("p1")
            snap = mem.recover("p1")
            assert snap is not None
            assert snap["state"]["identity"]["name"] == "Alice Smith"
        finally:
            mem.close()


# ---- Eligibility sweep tests ----

class TestEligibilitySweeps:
    def test_sweep_message_pending(self, tmp_path):
        store = Store(str(tmp_path / "sweep.db"))
        try:
            campaign = _campaign()
            store.save_campaign(campaign)
            store.assign_campaign_prospect(
                {"campaign_id": "c1", "prospect_id": "p1",
                 "status": "MESSAGE_PENDING"})
            items = sweep_message_pending(store, [campaign])
            assert len(items) == 1
            assert items[0]["reason"] == "message_pending"
        finally:
            store.close()

    def test_sweep_ignores_inactive_campaigns(self, tmp_path):
        store = Store(str(tmp_path / "sweep2.db"))
        try:
            campaign = _campaign(status="draft")
            store.save_campaign(campaign)
            store.assign_campaign_prospect(
                {"campaign_id": "c1", "prospect_id": "p1",
                 "status": "MESSAGE_PENDING"})
            items = sweep_message_pending(store, [campaign])
            assert len(items) == 0
        finally:
            store.close()


# ---- Handoff tests ----

class TestHandoff:
    def test_handoff_needed(self, tmp_path):
        store = EventStore(str(tmp_path / "ho.db"))
        try:
            result = handoff_needed(store, "test_reason", "p1", "c1")
            assert result is True
            events = store.events_for(prospect_id="p1",
                                      event_type="handoff_requested")
            assert len(events) == 1
        finally:
            store.close()

    def test_check_approval_required(self):
        campaign = _campaign()
        assert check_approval_required(campaign) is True
        campaign["approval"]["required"] = False
        assert check_approval_required(campaign) is False


# ---- Planner tests ----

class TestPlanner:
    def test_parse_plan_valid(self):
        text = '{"action": "wait", "reason": "test", "params": {}}'
        plan = _parse_plan(text)
        assert plan["action"] == "wait"

    def test_parse_plan_invalid(self):
        assert _parse_plan("") == {}
        assert _parse_plan("no json here") == {}
        assert _parse_plan(None) == {}

    def test_fallback_plan_no_task(self):
        context = {
            "outreach_state": "MESSAGE_PENDING",
            "state": {"identity": {"name": "X"}},
            "relationship": {"connection_status": "connected",
                             "relationship_stage": "none"},
            "product_journey": {"signed_up": False, "activated": False},
            "active_task": None,
            "facts": {"uses": [{"object": "Python"}]},
            "recent_events": [
                {"event_type": "qualified", "data": {"score": 60}},
            ],
        }
        plan = _fallback_plan(context)
        assert plan["action"] == "produce_message"

    def test_fallback_plan_no_facts(self):
        context = {
            "outreach_state": "MESSAGE_PENDING",
            "state": {"identity": {"name": "X"}},
            "relationship": {"connection_status": "connected"},
            "product_journey": {},
            "active_task": None,
            "facts": {},
            "recent_events": [],
        }
        plan = _fallback_plan(context)
        assert plan["action"] == "research_prospect"

    def test_fallback_plan_research_already_done(self):
        context = {
            "outreach_state": "MESSAGE_PENDING",
            "state": {"identity": {"name": "X"}},
            "relationship": {"connection_status": "connected"},
            "product_journey": {},
            "active_task": None,
            "facts": {},
            "recent_events": [
                {"event_type": "dry_run",
                 "data": {"action": "research_prospect", "reason": "test"}},
            ],
        }
        plan = _fallback_plan(context)
        assert plan["action"] == "produce_message"

    def test_fallback_plan_has_facts_not_qualified(self):
        context = {
            "outreach_state": "MESSAGE_PENDING",
            "state": {"identity": {"name": "X"}},
            "relationship": {"connection_status": "connected"},
            "product_journey": {},
            "active_task": None,
            "facts": {"observed": [{"object": "Python"}]},
            "recent_events": [
                {"event_type": "profile_observed", "data": {}},
            ],
        }
        plan = _fallback_plan(context)
        assert plan["action"] == "qualify_prospect"

    def test_fallback_plan_has_facts_qualified(self):
        context = {
            "outreach_state": "MESSAGE_PENDING",
            "state": {"identity": {"name": "X"}},
            "relationship": {"connection_status": "connected"},
            "product_journey": {},
            "active_task": None,
            "facts": {"observed": [{"object": "Python"}]},
            "recent_events": [
                {"event_type": "profile_observed", "data": {}},
                {"event_type": "qualified", "data": {"score": 60}},
            ],
        }
        plan = _fallback_plan(context)
        assert plan["action"] == "produce_message"

    def test_fallback_plan_message_review(self):
        context = {
            "outreach_state": "MESSAGE_REVIEW",
            "state": {"identity": {"name": "X"}},
            "relationship": {"connection_status": "connected"},
            "product_journey": {},
            "active_task": None,
            "facts": {},
            "recent_events": [],
        }
        plan = _fallback_plan(context)
        assert plan["action"] == "approve_message"

    def test_fallback_plan_with_task(self):
        context = {
            "outreach_state": "CAMPAIGN_ASSIGNED",
            "state": {},
            "relationship": {"connection_status": "connected"},
            "product_journey": {},
            "active_task": {"next_action": {"type": "follow_up",
                                            "reason": "due"}},
        }
        plan = _fallback_plan(context)
        assert plan["action"] == "follow_up"

    def test_fallback_plan_not_connected(self):
        context = {
            "outreach_state": "CAMPAIGN_ASSIGNED",
            "state": {},
            "relationship": {"connection_status": "not_connected"},
            "product_journey": {},
            "active_task": None,
        }
        plan = _fallback_plan(context)
        assert plan["action"] == "send_connection_request"

    def test_fallback_plan_signed_up_not_activated(self):
        context = {
            "outreach_state": "CONVERSATION",
            "state": {},
            "relationship": {"connection_status": "connected",
                             "relationship_stage": "conversation"},
            "product_journey": {"signed_up": True, "activated": False},
            "active_task": None,
        }
        plan = _fallback_plan(context)
        assert plan["action"] == "follow_up"


# ---- Executor tests ----

class TestExecutor:
    def test_execute_wait(self, tmp_path):
        store = Store(str(tmp_path / "exec.db"))
        mem = MemoryService(str(tmp_path / "mem_exec.db"))
        try:
            plan = {"action": "wait", "reason": "test"}
            result = execute_plan(store, mem, None, _prospect(), _campaign(),
                                  plan)
            assert result["status"] == "skipped"
        finally:
            store.close()
            mem.close()

    def test_execute_skip_transitions(self, tmp_path):
        store = Store(str(tmp_path / "exec2.db"))
        mem = MemoryService(str(tmp_path / "mem_exec2.db"))
        try:
            campaign = _campaign()
            store.save_campaign(campaign)
            store.upsert_prospects([_prospect()])
            store.assign_campaign_prospect(
                {"campaign_id": "c1", "prospect_id": "p1",
                 "status": "CAMPAIGN_ASSIGNED"})
            plan = {"action": "skip", "reason": "not a fit"}
            result = execute_plan(store, mem, None, _prospect(), _campaign(),
                                  plan)
            assert result["status"] == "skipped"
            cp2 = store.get_campaign_prospect("c1", "p1")
            assert cp2["status"] == "DO_NOT_CONTACT"
        finally:
            store.close()
            mem.close()

    def test_execute_handoff(self, tmp_path):
        store = Store(str(tmp_path / "exec3.db"))
        mem = MemoryService(str(tmp_path / "mem_exec3.db"))
        try:
            plan = {"action": "handoff", "reason": "uncertain"}
            result = execute_plan(store, mem, None, _prospect(), _campaign(),
                                  plan)
            assert result["status"] == "handed_off"
            events = mem.events.events_for(prospect_id="p1",
                                           event_type="handoff_requested")
            assert len(events) == 1
        finally:
            store.close()
            mem.close()


# ---- LinkedIn actions tests ----

class TestLinkedInActions:
    def test_action_map_has_all_actions(self):
        expected = {"observe_profile", "observe_conversation",
                    "send_connection_request", "send_message",
                    "endorse_skill", "view_profile"}
        assert set(ACTION_MAP.keys()) == expected


# ---- Orchestrator dry-run tests ----

class TestOrchestrator:
    def test_dry_run_does_not_execute(self, tmp_path):
        store = Store(str(tmp_path / "orch.db"))
        mem = MemoryService(str(tmp_path / "mem_orch.db"))
        try:
            campaign = _campaign()
            store.save_campaign(campaign)
            store.upsert_prospects([_prospect()])
            store.assign_campaign_prospect(
                {"campaign_id": "c1", "prospect_id": "p1",
                 "status": "MESSAGE_PENDING"})
            orch = AgentOrchestrator(store=store, memory=mem,
                                     browser=None, dry_run=True,
                                     max_cycles=1)
            result = orch.run_once()
            assert result["work_done"] >= 1
            assert all(not a.get("executed", True)
                       for a in result["actions"])
        finally:
            store.close()
            mem.close()

    def test_manual_mode_creates_handoff(self, tmp_path):
        store = Store(str(tmp_path / "orch2.db"))
        mem = MemoryService(str(tmp_path / "mem_orch2.db"))
        try:
            campaign = _campaign()
            store.save_campaign(campaign)
            store.upsert_prospects([_prospect()])
            store.assign_campaign_prospect(
                {"campaign_id": "c1", "prospect_id": "p1",
                 "status": "MESSAGE_PENDING"})
            orch = AgentOrchestrator(store=store, memory=mem,
                                     browser=None, mode="manual",
                                     max_cycles=1)
            from unittest.mock import patch
            fake_plan = {"action": "produce_message",
                         "reason": "test", "params": {}}
            with patch("linkedin_intelligence.agent.orchestrator.plan_next_action",
                       return_value=fake_plan):
                with patch("builtins.input", return_value="n"):
                    result = orch.run_once()
            assert result["actions"][0]["executed"] is False
        finally:
            store.close()
            mem.close()
