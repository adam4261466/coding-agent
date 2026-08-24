"""Unified memory service: one API for all agent context.

The planner doesn't need to know where any data came from.
It calls:

  memory.get_prospect_context(prospect_id)

and gets:

  CURRENT STATE + FACTS + RECENT EVENTS + ACTIVE TASKS + PENDING ACTIONS

This is the ONLY interface the planner uses.
"""


from ...timeutil import iso as _now_iso
from ...db import connect as db_connect

from datetime import datetime, timezone

from .event_store import EventStore
from .state_store import StateStore
from .fact_store import FactStore, SOURCE_TRUST
from .task_store import TaskStore
from .snapshot_store import SnapshotStore


class MemoryService:
    """Unified API for all agent memory. One object, one call."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.events = EventStore(db_path)
        self.state = StateStore(db_path)
        self.facts = FactStore(db_path)
        self.tasks = TaskStore(db_path)
        self.snapshots = SnapshotStore(db_path)

    def close(self):
        self.events.close()
        self.state.close()
        self.facts.close()
        self.tasks.close()
        self.snapshots.close()

    # ---- THE main API ----

    def get_prospect_context(self, prospect_id: str,
                             campaign_id: str = None) -> dict:
        """Build complete context for the planner. This is what the
        planner sees for every decision."""
        state = self.state.get_state(prospect_id)
        facts = self.facts.fact_summary(prospect_id)
        recent = self.events.recent_events(prospect_id, n=20)
        active = self.tasks.active_tasks(prospect_id=prospect_id)
        task = active[0] if active else None
        task_progress = self.tasks.progress(task["task_id"]) if task else None

        outreach_state = None
        if campaign_id:
            cp = self._get_campaign_prospect(campaign_id, prospect_id)
            if cp:
                outreach_state = cp.get("status")

        return {
            "state": state,
            "facts": facts,
            "outreach_state": outreach_state,
            "recent_events": [
                {"type": e["event_type"], "data": e.get("data", {}),
                 "at": e["created_at"]}
                for e in recent
            ],
            "active_task": task_progress,
            "product_journey": state.get("product", {}),
            "conversation_summary": state.get("conversation", {}),
            "relationship": state.get("relationship", {}),
        }

    def _get_campaign_prospect(self, campaign_id: str,
                               prospect_id: str) -> dict | None:
        """Read campaign-prospect state from the main store DB."""
        try:
            conn = db_connect(self.db_path.replace("_agent.db", ".db"))
            row = conn.execute(
                "SELECT * FROM campaign_prospects "
                "WHERE campaign_id = ? AND prospect_id = ?",
                (campaign_id, prospect_id)).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception:
            return None

    # ---- Event recording shortcuts ----

    def record_event(self, event_type: str, prospect_id: str = None,
                     campaign_id: str = None, data: dict = None,
                     source: str = None, confidence: float = None):
        """Record an event AND update state from it."""
        self.events.append(event_type, prospect_id, campaign_id,
                           data, source, confidence)
        self._update_state_from_event(prospect_id, event_type, data or {})

    def _update_state_from_event(self, prospect_id: str,
                                 event_type: str, data: dict):
        """Derive state changes from events. State is always current."""
        if event_type == "profile_observed":
            updates = {}
            if data.get("company"):
                updates["company"] = data["company"]
            if data.get("role"):
                updates["role"] = data["role"]
            if data.get("name"):
                updates["name"] = data["name"]
            if updates:
                self.state.update_identity(prospect_id, **updates)

        elif event_type == "qualified":
            self.state.update_qualification(
                prospect_id,
                score=data.get("score"),
                segment=data.get("segment"),
                qualified=data.get("qualified", False))

        elif event_type == "connection_sent":
            self.state.update_relationship(
                prospect_id,
                connection_status="pending",
                last_contact_at=_now_iso())
            rel = self.state.get_state(prospect_id).get("relationship", {})
            self.state.update_relationship(
                prospect_id,
                contact_count=rel.get("contact_count", 0) + 1)

        elif event_type == "connection_accepted":
            self.state.update_relationship(
                prospect_id,
                connection_status="connected",
                relationship_stage="new_connection")

        elif event_type == "message_sent":
            self.state.update_relationship(
                prospect_id,
                last_contact_at=_now_iso())
            rel = self.state.get_state(prospect_id).get("relationship", {})
            self.state.update_relationship(
                prospect_id,
                contact_count=rel.get("contact_count", 0) + 1)
            camp = self.state.get_state(prospect_id).get("campaign", {})
            if camp.get("campaign_id"):
                self.state.update_campaign(
                    prospect_id,
                    sequence=camp.get("sequence", 0) + 1)

        elif event_type == "reply_detected":
            self.state.update_conversation(
                prospect_id,
                status="active",
                last_message_direction="prospect",
                last_message_at=_now_iso())

        elif event_type == "reply_classified":
            self.state.update_conversation(
                prospect_id,
                intent=data.get("intent"),
                objection=data.get("objection"))

        elif event_type == "website_visit":
            self.state.update_product(prospect_id, visited=True)

        elif event_type == "signup_detected":
            self.state.update_product(prospect_id, signed_up=True)

        elif event_type == "activation_detected":
            self.state.update_product(prospect_id, activated=True)

    # ---- Fact recording shortcuts ----

    def record_fact(self, prospect_id: str, predicate: str, obj: str,
                    confidence: float = 1.0, source: str = "unknown",
                    evidence: str = None):
        """Record a fact. Supersedes any existing fact with same predicate+object."""
        return self.facts.add_fact(prospect_id, predicate, obj,
                                   confidence, source, evidence)

    def check_fact(self, prospect_id: str, predicate: str, obj: str) -> bool:
        """Check if a fact is known."""
        return self.facts.has_fact(prospect_id, predicate, obj)

    # ---- Task shortcuts ----

    def start_task(self, prospect_id: str, goal: str,
                   campaign_id: str = None,
                   steps: list = None) -> dict:
        """Create and return a new task."""
        return self.tasks.create_task(prospect_id, goal, campaign_id, steps)

    def advance_task(self, task_id: str, step: str):
        """Mark a step as completed."""
        self.tasks.complete_step(task_id, step)

    # ---- Reconciliation ----

    def reconcile_state(self, prospect_id: str, section: str, field: str,
                        new_value, source: str, confidence: float = 1.0):
        """Correct state from an authoritative source. Records a reconciliation event."""
        result = self.state.reconcile(prospect_id, section, field,
                                      new_value, source, confidence)
        if result:
            self.events.append(
                "state_reconciliation", prospect_id,
                data=result, source=source, confidence=confidence)
        return result

    # ---- Snapshot / crash recovery ----

    def save_snapshot(self, prospect_id: str):
        """Save a full snapshot for crash recovery."""
        state = self.state.get_state(prospect_id)
        active = self.tasks.active_tasks(prospect_id=prospect_id)
        task = active[0] if active else None
        recent = self.events.recent_events(prospect_id, n=20)
        facts = self.facts.get_facts(prospect_id)
        return self.snapshots.save_snapshot(
            prospect_id, state, task, recent, facts)

    def recover(self, prospect_id: str) -> dict | None:
        """Load the latest snapshot. Returns None if no snapshot exists."""
        return self.snapshots.latest_snapshot(prospect_id)

    # ---- Initialization from existing data ----

    def init_from_store(self, store, prospect: dict):
        """Bootstrap memory state from an existing prospect dict.
        Called once when a prospect first enters the agent system."""
        pid = prospect["prospect_id"]
        self.state.update_identity(
            pid,
            name=prospect.get("full_name"),
            linkedin_url=prospect.get("linkedin_url"),
            company=prospect.get("current_company"),
            role=prospect.get("raw_position"))
        self.state.update_qualification(
            pid,
            score=prospect.get("total_score"),
            segment=(prospect.get("segments") or [None])[0],
            qualified=prospect.get("status") in (
                "ready_for_outreach", "approved", "human_approved"))
        conn_status = prospect.get("connection_status", "unknown")
        if conn_status == "1st_degree":
            conn_status = "connected"
        self.state.update_relationship(
            pid, connection_status=conn_status)
        self.events.append("prospect_discovered", pid,
                           data={"source": "phase1_import"},
                           source="system")
