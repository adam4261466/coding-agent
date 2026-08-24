"""Immutable append-only event stream.

Every action, observation, decision, and state change is recorded here.
Events are NEVER modified or deleted. If a mistake is made, a correction
event is appended - the history remains truthful.

EVENT TYPES:
  prospect_discovered    - new prospect entered the system
  profile_observed       - browser observed a LinkedIn profile
  qualified              - Phase 2 qualification completed
  assigned_to_campaign   - prospect added to a campaign
  message_generated      - outreach message produced
  message_approved       - human approved the message
  message_sent           - message sent via LinkedIn
  reply_detected         - inbound reply found
  reply_classified       - LLM classified the reply
  connection_sent        - connection request sent
  connection_accepted    - connection request accepted
  website_visit          - prospect visited our website
  signup_detected        - prospect signed up
  activation_detected    - prospect activated
  state_reconciliation   - state corrected from authoritative source
  handoff_requested      - agent needs human intervention
  human_cleared          - a human resolved a handoff; orchestrator may
                           pick this prospect back up next cycle
  action_executed        - browser action completed
  error                   - something went wrong
  agent_decision          - planner decided what to do

ACTION LOG (`action_log` table):
  A second, narrower table written alongside every `record_action()` call.
  `events` is the human-readable audit trail (event_type is a semantic
  label like "message_sent"); `action_log` exists purely so rate limiting
  can ask "when did action X last run for prospect Y, and how many times
  has it run account-wide today?" with an indexed query instead of a
  string match against event_type - which never matches, because nothing
  in this codebase writes an event with event_type == "send_message" (the
  action name); it writes event_type == "message_sent" (the outcome). See
  automation/monitoring/handoff.py for the rate limiter that reads this.
"""


from ...timeutil import iso as _now_iso
import json
import sqlite3
from datetime import datetime, timezone

from ...db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    prospect_id TEXT,
    campaign_id TEXT,
    data_json TEXT,
    source TEXT,
    confidence REAL,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_prospect ON events(prospect_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_campaign ON events(campaign_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    prospect_id TEXT,
    success INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_log_action_time ON action_log(action, created_at);
CREATE INDEX IF NOT EXISTS idx_action_log_prospect_action ON action_log(prospect_id, action, created_at);
"""


class EventStore:
    """Immutable append-only event log. Events are never updated or deleted."""

    def __init__(self, db_path: str):
        self.conn = connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def append(self, event_type: str, prospect_id: str = None,
               campaign_id: str = None, data: dict = None,
               source: str = None, confidence: float = None) -> dict:
        """Append one event. Returns the event dict."""
        event = {
            "event_type": event_type,
            "prospect_id": prospect_id,
            "campaign_id": campaign_id,
            "data": data or {},
            "source": source,
            "confidence": confidence,
            "created_at": _now_iso(),
        }
        self.conn.execute(
            """INSERT INTO events
               (event_type, prospect_id, campaign_id, data_json,
                source, confidence, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (event_type, prospect_id, campaign_id,
             json.dumps(data or {}, ensure_ascii=False),
             source, confidence, event["created_at"]))
        self.conn.commit()
        return event

    def events_for(self, prospect_id: str = None, campaign_id: str = None,
                   event_type: str = None, limit: int = 100,
                   offset: int = 0) -> list:
        """Query events. Always ordered by id ASC (chronological)."""
        sql = "SELECT * FROM events WHERE 1=1"
        args = []
        if prospect_id:
            sql += " AND prospect_id = ?"
            args.append(prospect_id)
        if campaign_id:
            sql += " AND campaign_id = ?"
            args.append(campaign_id)
        if event_type:
            sql += " AND event_type = ?"
            args.append(event_type)
        sql += " ORDER BY id ASC LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        rows = self.conn.execute(sql, args).fetchall()
        return [self._deserialize(dict(r)) for r in rows]

    def recent_events(self, prospect_id: str, n: int = 10) -> list:
        """Get the N most recent events for a prospect (newest last)."""
        rows = self.conn.execute(
            "SELECT * FROM events WHERE prospect_id = ? ORDER BY id DESC LIMIT ?",
            (prospect_id, n)).fetchall()
        return [self._deserialize(dict(r)) for r in reversed(rows)]

    def last_event_of_type(self, prospect_id: str, event_type: str) -> dict | None:
        """Get the most recent event of a specific type for a prospect."""
        row = self.conn.execute(
            "SELECT * FROM events WHERE prospect_id = ? AND event_type = ? "
            "ORDER BY id DESC LIMIT 1",
            (prospect_id, event_type)).fetchone()
        return self._deserialize(dict(row)) if row else None

    def count_events(self, prospect_id: str, event_type: str = None) -> int:
        sql = "SELECT COUNT(*) FROM events WHERE prospect_id = ?"
        args = [prospect_id]
        if event_type:
            sql += " AND event_type = ?"
            args.append(event_type)
        return self.conn.execute(sql, args).fetchone()[0]

    def record_action(self, action_name: str, prospect_id: str,
                      data: dict = None, success: bool = True) -> dict:
        """Record an action execution event AND log it for rate limiting.

        Writes to both `events` (event_type="action_executed", for the
        human-readable audit trail - unchanged from before) and the new
        `action_log` table (for indexed, fast rate-limit lookups).
        `success` defaults to True for callers that only invoke this on
        the success path; pass success=False explicitly when recording a
        failed attempt so it still counts toward daily caps but doesn't
        block a retry's per-prospect cooldown (see handoff.py).
        """
        event = self.append("action_executed", prospect_id,
                            data={"action": action_name, "success": success,
                                  **(data or {})},
                            source="agent")
        self.conn.execute(
            "INSERT INTO action_log (action, prospect_id, success, created_at) "
            "VALUES (?,?,?,?)",
            (action_name, prospect_id, 1 if success else 0, event["created_at"]))
        self.conn.commit()
        return event

    def last_action_time(self, action: str, prospect_id: str,
                         success_only: bool = True) -> datetime | None:
        """Most recent time `action` ran for `prospect_id`, or None.

        With success_only=True (the default, used for cooldown checks), a
        failed attempt does not count - so a failure can be retried
        immediately instead of waiting out the full cooldown.
        """
        sql = "SELECT created_at FROM action_log WHERE action = ? AND prospect_id = ?"
        args = [action, prospect_id]
        if success_only:
            sql += " AND success = 1"
        sql += " ORDER BY created_at DESC LIMIT 1"
        row = self.conn.execute(sql, args).fetchone()
        if not row:
            return None
        return _parse_iso(row["created_at"])

    def action_count_since(self, action: str, since: datetime,
                           prospect_id: str = None) -> int:
        """Count of `action` executions (success or fail) since `since`,
        account-wide by default (prospect_id=None) - this is what a daily
        cap should check, since the risk is to the LinkedIn account as a
        whole, not any one prospect."""
        sql = "SELECT COUNT(*) FROM action_log WHERE action = ? AND created_at >= ?"
        args = [action, since.isoformat()]
        if prospect_id:
            sql += " AND prospect_id = ?"
            args.append(prospect_id)
        return self.conn.execute(sql, args).fetchone()[0]

    def _deserialize(self, d: dict) -> dict:
        if isinstance(d.get("data_json"), str):
            try:
                d["data"] = json.loads(d["data_json"])
            except (json.JSONDecodeError, TypeError):
                d["data"] = {}
        d.pop("data_json", None)
        return d


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
