"""Fact store: evidence-backed knowledge graph per prospect.

Facts are different from events. An event says "we observed X at time T."
A fact says "we believe Y is true, backed by evidence E, with confidence C."

Facts can be updated when new evidence arrives — the old fact becomes
history and the new one takes its place.

TRUST LEVELS (source → base confidence):
  app_database          1.00
  payment_system        1.00
  linkedin_observation  0.95
  conversation          0.90
  llm_qualification     0.60
  llm_inference         0.50
  llm_speculation       0.20

A fact's effective confidence = base_confidence × evidence_confidence.

FACT TYPES:
  identity        — name, company, role
  technology      — uses Python, uses AWS
  pain            — has_problem: documentation_management
  interest        — interested_in: ai_platform
  behavior        — visited_pricing, signed_up
  relationship    — connected, replied, engaged
"""


from ...timeutil import iso as _now_iso
import json
import sqlite3
from datetime import datetime, timezone

from ...db import connect as db_connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id TEXT PRIMARY KEY,
    prospect_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    confidence REAL,
    source TEXT,
    source_confidence REAL,
    evidence TEXT,
    created_at TEXT,
    superseded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_facts_prospect ON facts(prospect_id);
CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate);
CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(prospect_id, superseded_at);
"""

# Base confidence per source type
SOURCE_TRUST = {
    "app_database": 1.00,
    "payment_system": 1.00,
    "linkedin_observation": 0.95,
    "conversation": 0.90,
    "llm_qualification": 0.60,
    "llm_inference": 0.50,
    "llm_speculation": 0.20,
}


class FactStore:
    """Knowledge graph. Stores evidence-backed facts per prospect."""

    def __init__(self, db_path: str):
        self.conn = db_connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def add_fact(self, prospect_id: str, predicate: str, obj: str,
                 confidence: float = 1.0, source: str = "unknown",
                 evidence: str = None) -> dict:
        """Add a fact. If an active fact with the same predicate+object
        exists, supersede it. Returns the fact dict."""
        import uuid
        now = _now_iso()
        source_trust = SOURCE_TRUST.get(source, 0.5)
        effective_confidence = round(source_trust * confidence, 3)

        existing = self.conn.execute(
            "SELECT fact_id FROM facts "
            "WHERE prospect_id = ? AND predicate = ? "
            "AND superseded_at IS NULL",
            (prospect_id, predicate)).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE facts SET superseded_at = ? WHERE fact_id = ?",
                (now, existing["fact_id"]))

        fact_id = "fact_" + uuid.uuid4().hex[:10]
        self.conn.execute(
            """INSERT INTO facts
               (fact_id, prospect_id, predicate, object, confidence,
                source, source_confidence, evidence, created_at, superseded_at)
               VALUES (?,?,?,?,?,?,?,?,?,NULL)""",
            (fact_id, prospect_id, predicate, obj, effective_confidence,
             source, source_trust, evidence, now))
        self.conn.commit()
        return {
            "fact_id": fact_id, "prospect_id": prospect_id,
            "predicate": predicate, "object": obj,
            "confidence": effective_confidence,
            "source": source, "source_confidence": source_trust,
            "evidence": evidence, "created_at": now,
        }

    def get_facts(self, prospect_id: str, predicate: str = None) -> list:
        """Get all ACTIVE (non-superseded) facts for a prospect."""
        sql = "SELECT * FROM facts WHERE prospect_id = ? AND superseded_at IS NULL"
        args = [prospect_id]
        if predicate:
            sql += " AND predicate = ?"
            args.append(predicate)
        sql += " ORDER BY confidence DESC"
        rows = self.conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def get_fact(self, prospect_id: str, predicate: str,
                 obj: str = None) -> dict | None:
        """Get the single active fact for a predicate (optionally filtered by object)."""
        sql = ("SELECT * FROM facts WHERE prospect_id = ? AND predicate = ? "
               "AND superseded_at IS NULL")
        args = [prospect_id, predicate]
        if obj:
            sql += " AND object = ?"
            args.append(obj)
        sql += " ORDER BY confidence DESC LIMIT 1"
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def has_fact(self, prospect_id: str, predicate: str, obj: str) -> bool:
        """Check if an active fact exists."""
        row = self.conn.execute(
            "SELECT 1 FROM facts WHERE prospect_id = ? AND predicate = ? "
            "AND object = ? AND superseded_at IS NULL",
            (prospect_id, predicate, obj)).fetchone()
        return row is not None

    def supersede_all(self, prospect_id: str, predicate: str):
        """Supersede all active facts for a predicate (mark as historical)."""
        now = _now_iso()
        self.conn.execute(
            "UPDATE facts SET superseded_at = ? "
            "WHERE prospect_id = ? AND predicate = ? AND superseded_at IS NULL",
            (now, prospect_id, predicate))
        self.conn.commit()

    def fact_history(self, prospect_id: str, predicate: str = None) -> list:
        """Get ALL facts (including superseded) for audit trail."""
        sql = "SELECT * FROM facts WHERE prospect_id = ?"
        args = [prospect_id]
        if predicate:
            sql += " AND predicate = ?"
            args.append(predicate)
        sql += " ORDER BY created_at ASC"
        rows = self.conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def fact_summary(self, prospect_id: str) -> dict:
        """Compact summary of all active facts grouped by predicate."""
        facts = self.get_facts(prospect_id)
        summary = {}
        for f in facts:
            pred = f["predicate"]
            if pred not in summary:
                summary[pred] = []
            summary[pred].append({
                "object": f["object"],
                "confidence": f["confidence"],
                "source": f["source"],
            })
        return summary
