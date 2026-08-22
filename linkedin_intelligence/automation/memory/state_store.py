"""Current truth store: derived state per prospect.

Unlike events (which are immutable), state is the CURRENT snapshot of
what is true about a prospect right now. It is derived from events and
facts, and can be corrected by reconciliation.

The state store answers: "What is true NOW?"

STATE SECTIONS:
  identity       — who is this person
  qualification  — how they scored and why
  relationship   — connection status, contact history
  conversation   — last interaction, intent, objections
  product        — website visits, signup, activation
  campaign       — which campaign, sequence progress, next action
"""

import json
import sqlite3
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from db import connect as db_connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospect_state (
    prospect_id TEXT PRIMARY KEY,
    identity_json TEXT,
    qualification_json TEXT,
    relationship_json TEXT,
    conversation_json TEXT,
    product_json TEXT,
    campaign_json TEXT,
    updated_at TEXT
);
"""


class StateStore:
    """Current truth about each prospect. Updated on every state change."""

    def __init__(self, db_path: str):
        self.conn = db_connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def get_state(self, prospect_id: str) -> dict:
        """Get the full current state for a prospect. Returns empty state
        if no state exists yet."""
        row = self.conn.execute(
            "SELECT * FROM prospect_state WHERE prospect_id = ?",
            (prospect_id,)).fetchone()
        if not row:
            return _empty_state(prospect_id)
        return {
            "prospect_id": prospect_id,
            "identity": _parse_json(row["identity_json"]),
            "qualification": _parse_json(row["qualification_json"]),
            "relationship": _parse_json(row["relationship_json"]),
            "conversation": _parse_json(row["conversation_json"]),
            "product": _parse_json(row["product_json"]),
            "campaign": _parse_json(row["campaign_json"]),
            "updated_at": row["updated_at"],
        }

    def update_identity(self, prospect_id: str, **kwargs):
        state = self.get_state(prospect_id)
        state["identity"].update(kwargs)
        self._save(prospect_id, "identity_json", state["identity"])

    def update_qualification(self, prospect_id: str, **kwargs):
        state = self.get_state(prospect_id)
        state["qualification"].update(kwargs)
        self._save(prospect_id, "qualification_json", state["qualification"])

    def update_relationship(self, prospect_id: str, **kwargs):
        state = self.get_state(prospect_id)
        state["relationship"].update(kwargs)
        self._save(prospect_id, "relationship_json", state["relationship"])

    def update_conversation(self, prospect_id: str, **kwargs):
        state = self.get_state(prospect_id)
        state["conversation"].update(kwargs)
        self._save(prospect_id, "conversation_json", state["conversation"])

    def update_product(self, prospect_id: str, **kwargs):
        state = self.get_state(prospect_id)
        state["product"].update(kwargs)
        self._save(prospect_id, "product_json", state["product"])

    def update_campaign(self, prospect_id: str, **kwargs):
        state = self.get_state(prospect_id)
        state["campaign"].update(kwargs)
        self._save(prospect_id, "campaign_json", state["campaign"])

    def reconcile(self, prospect_id: str, section: str, field: str,
                  new_value, source: str, confidence: float = 1.0) -> dict | None:
        """Correct a field from an authoritative source.

        Returns the reconciliation event data if the value changed,
        None if it was already correct.
        """
        state = self.get_state(prospect_id)
        current = state.get(section, {}).get(field)
        if current == new_value:
            return None
        state[section][field] = new_value
        state[section][f"{field}_source"] = source
        state[section][f"{field}_confidence"] = confidence
        self._save(prospect_id, f"{section}_json", state[section])
        return {
            "section": section,
            "field": field,
            "old_value": current,
            "new_value": new_value,
            "source": source,
            "confidence": confidence,
        }

    def _save(self, prospect_id: str, column: str, data: dict):
        now = datetime.now(timezone.utc).isoformat()
        existing = self.conn.execute(
            "SELECT prospect_id FROM prospect_state WHERE prospect_id = ?",
            (prospect_id,)).fetchone()
        if existing:
            self.conn.execute(
                f"UPDATE prospect_state SET {column} = ?, updated_at = ? "
                "WHERE prospect_id = ?",
                (json.dumps(data, ensure_ascii=False), now, prospect_id))
        else:
            state = _empty_state(prospect_id)
            state[column.replace("_json", "")] = data
            self.conn.execute(
                """INSERT INTO prospect_state
                   (prospect_id, identity_json, qualification_json,
                    relationship_json, conversation_json, product_json,
                    campaign_json, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (prospect_id,
                 json.dumps(state["identity"], ensure_ascii=False),
                 json.dumps(state["qualification"], ensure_ascii=False),
                 json.dumps(state["relationship"], ensure_ascii=False),
                 json.dumps(state["conversation"], ensure_ascii=False),
                 json.dumps(state["product"], ensure_ascii=False),
                 json.dumps(state["campaign"], ensure_ascii=False),
                 now))
        self.conn.commit()


def _empty_state(prospect_id: str) -> dict:
    return {
        "prospect_id": prospect_id,
        "identity": {
            "name": None, "linkedin_url": None,
            "company": None, "role": None,
        },
        "qualification": {
            "score": None, "segment": None, "qualified": False,
        },
        "relationship": {
            "connection_status": "unknown",
            "relationship_stage": "none",
            "first_contact_at": None,
            "last_contact_at": None,
            "contact_count": 0,
        },
        "conversation": {
            "status": "none",
            "last_message_direction": None,
            "last_message_at": None,
            "intent": None,
            "objection": None,
        },
        "product": {
            "visited": False,
            "signed_up": False,
            "activated": False,
            "customer": False,
        },
        "campaign": {
            "campaign_id": None,
            "sequence": 0,
            "next_action": None,
            "next_action_at": None,
        },
        "updated_at": None,
    }


def _parse_json(s):
    if not s:
        return {}
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}
