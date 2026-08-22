"""Snapshot store: periodic state snapshots for crash recovery.

Snapshots capture the full agent context at a point in time. On restart,
the agent loads the latest snapshot and continues from there.

A snapshot contains:
  - Current state (from state_store)
  - Active task (from task_store)
  - Recent events (from event_store)
  - Key facts (from fact_store)
"""

import json
import sqlite3
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from db import connect as db_connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    prospect_id TEXT NOT NULL,
    state_json TEXT,
    task_json TEXT,
    events_json TEXT,
    facts_json TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_prospect ON snapshots(prospect_id);
"""


class SnapshotStore:
    """Periodic snapshots for crash recovery."""

    def __init__(self, db_path: str):
        self.conn = db_connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def save_snapshot(self, prospect_id: str, state: dict, task: dict = None,
                      events: list = None, facts: list = None) -> dict:
        """Save a point-in-time snapshot. Returns the snapshot dict."""
        import uuid
        now = datetime.now(timezone.utc).isoformat()
        snapshot_id = "snap_" + uuid.uuid4().hex[:10]
        snapshot = {
            "snapshot_id": snapshot_id,
            "prospect_id": prospect_id,
            "state": state,
            "task": task,
            "events": events or [],
            "facts": facts or [],
            "created_at": now,
        }
        self.conn.execute(
            """INSERT INTO snapshots
               (snapshot_id, prospect_id, state_json, task_json,
                events_json, facts_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (snapshot_id, prospect_id,
             json.dumps(state, ensure_ascii=False),
             json.dumps(task, ensure_ascii=False) if task else None,
             json.dumps(events or [], ensure_ascii=False),
             json.dumps(facts or [], ensure_ascii=False),
             now))
        self.conn.commit()
        return snapshot

    def latest_snapshot(self, prospect_id: str) -> dict | None:
        """Get the most recent snapshot for a prospect."""
        row = self.conn.execute(
            "SELECT * FROM snapshots WHERE prospect_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (prospect_id,)).fetchone()
        return self._deserialize(dict(row)) if row else None

    def snapshots_for(self, prospect_id: str, limit: int = 10) -> list:
        """Get recent snapshots for a prospect."""
        rows = self.conn.execute(
            "SELECT * FROM snapshots WHERE prospect_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (prospect_id, limit)).fetchall()
        return [self._deserialize(dict(r)) for r in rows]

    def _deserialize(self, d: dict) -> dict:
        for k in ("state_json", "task_json", "events_json", "facts_json"):
            if isinstance(d.get(k), str):
                try:
                    d[k.replace("_json", "")] = json.loads(d[k])
                except (json.JSONDecodeError, TypeError):
                    d[k.replace("_json", "")] = [] if "events" in k or "facts" in k else {}
            else:
                d[k.replace("_json", "")] = d.pop(k, None)
        return d
