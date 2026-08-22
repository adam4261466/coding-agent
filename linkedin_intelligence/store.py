"""SQLite storage for prospects, conversations, exclusions, evidence, tasks."""

import json
import os
import sqlite3
from datetime import datetime, timezone

from .utils import DB_PATH, ensure_dirs
from .db import connect as db_connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    id TEXT PRIMARY KEY,
    full_name TEXT,
    first_name TEXT,
    last_name TEXT,
    linkedin_url TEXT,
    email TEXT,
    current_company TEXT,
    normalized_company TEXT,
    company_type TEXT,
    raw_position TEXT,
    normalized_position TEXT,
    role_category TEXT,
    connection_status TEXT,
    connected_date TEXT,
    relationship_score REAL,
    role_score REAL,
    company_score REAL,
    relevance_score REAL,
    total_score REAL,
    commercial_signal TEXT,
    status TEXT,
    last_contacted TEXT,
    next_action TEXT,
    segment_band TEXT,
    source TEXT,
    data_json TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id TEXT,
    conversation_id TEXT,
    participants TEXT,
    topic TEXT,
    last_date TEXT,
    direction TEXT,
    message_count INTEGER,
    commercial_signal TEXT,
    relationship_state TEXT,
    follow_up_needed INTEGER,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS exclusions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id TEXT,
    name TEXT,
    reasons TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    prospect_id TEXT,
    claim TEXT,
    observation TEXT,
    source TEXT,
    source_type TEXT,
    confidence REAL,
    collector TEXT,
    research_task_id TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS research_tasks (
    task_id TEXT PRIMARY KEY,
    prospect_id TEXT,
    objective TEXT,
    required_fields TEXT,
    budget TEXT,
    stage TEXT,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS qualifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id TEXT,
    fit_score REAL,
    problem_fit_score REAL,
    confidence REAL,
    research_mode TEXT,
    method TEXT,
    reason TEXT,
    evidence_used TEXT,
    uncertainties TEXT,
    recommended_next_action TEXT,
    contradictions TEXT,
    research_stage TEXT,
    model TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    description TEXT,
    prospects TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS human_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id TEXT,
    action TEXT,
    reason TEXT,
    status_before TEXT,
    created_at TEXT
);

-- Phase 3: outreach & conversion engine (kept separate from Phase 1/2 tables).
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    name TEXT,
    objective TEXT,
    strategy TEXT,
    status TEXT,
    config_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS campaign_prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT,
    prospect_id TEXT,
    status TEXT,
    priority REAL,
    assigned_strategy TEXT,
    entered_at TEXT,
    last_action TEXT,
    UNIQUE(campaign_id, prospect_id)
);

CREATE TABLE IF NOT EXISTS outreach_messages (
    message_id TEXT PRIMARY KEY,
    prospect_id TEXT,
    campaign_id TEXT,
    strategy TEXT,
    version TEXT,
    text TEXT,
    claims TEXT,
    evidence_used TEXT,
    confidence REAL,
    approved INTEGER,
    validation TEXT,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS outreach_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id TEXT,
    campaign_id TEXT,
    from_state TEXT,
    to_state TEXT,
    event TEXT,
    note TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS outreach_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id TEXT,
    campaign_id TEXT,
    intent TEXT,
    sentiment TEXT,
    pain_signal TEXT,
    commercial_intent TEXT,
    objection TEXT,
    confidence REAL,
    raw TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS product_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id TEXT,
    campaign_id TEXT,
    message_id TEXT,
    event_name TEXT,
    meta_json TEXT,
    created_at TEXT
);

-- Phase 4: experiments (growth optimization engine).
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    hypothesis TEXT,
    control TEXT,
    variant TEXT,
    segment TEXT,
    primary_metric TEXT,
    minimum_sample INTEGER,
    status TEXT,
    result_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: str = None):
        ensure_dirs()
        self.path = path or DB_PATH
        self.conn = db_connect(self.path)
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def close(self):
        self.conn.close()

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, value))
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def reset(self):
        """Drop all rows so a fresh Phase 1 build starts clean."""
        for table in ("prospects", "conversations", "exclusions", "evidence",
                      "research_tasks", "qualifications", "segments",
                      "human_feedback", "campaigns", "campaign_prospects",
                      "outreach_messages", "outreach_events",
                      "outreach_conversations", "product_events",
                      "experiments"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    def _migrate(self):
        """Rebuild tables whose schema changed across versions."""
        cols = [r[1] for r in self.conn.execute(
            "PRAGMA table_info(evidence)").fetchall()]
        if cols and "observation" not in cols:
            self.conn.execute("DROP TABLE evidence")
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        qcols = [r[1] for r in self.conn.execute(
            "PRAGMA table_info(qualifications)").fetchall()]
        if qcols and "confidence" not in qcols:
            self.conn.execute("DROP TABLE qualifications")
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        if qcols and "contradictions" not in qcols:
            self.conn.execute(
                "ALTER TABLE qualifications ADD COLUMN contradictions TEXT")
            self.conn.commit()
        if qcols and "research_stage" not in qcols:
            self.conn.execute(
                "ALTER TABLE qualifications ADD COLUMN research_stage TEXT")
            self.conn.commit()
        tcols = [r[1] for r in self.conn.execute(
            "PRAGMA table_info(research_tasks)").fetchall()]
        if tcols and "stage" not in tcols:
            self.conn.execute("ALTER TABLE research_tasks ADD COLUMN stage TEXT")
            self.conn.commit()

    # ---- prospects ----
    def upsert_prospects(self, prospects: list):
        for p in prospects:
            self.conn.execute(
                """INSERT OR REPLACE INTO prospects VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p["prospect_id"], p["full_name"], p.get("first_name"),
                 p.get("last_name"), p.get("linkedin_url"), p.get("email"),
                 p.get("current_company"), p.get("normalized_company"),
                 p.get("company_type"), p.get("raw_position"),
                 p.get("normalized_position"), p.get("role_category"),
                 p.get("connection_status"), p.get("connected_date"),
                 p.get("relationship_score"), p.get("role_fit"),
                 p.get("company_fit"), p.get("relevance_score"),
                 p.get("total_score"), p.get("commercial_intent"),
                 p.get("status"), p.get("last_contacted"), p.get("next_action"),
                 p.get("segment_band"), p.get("source"),
                 json.dumps(p, ensure_ascii=False)))
        self.conn.commit()

    def prospects(self, status: str = None, role_category: str = None,
                  min_score: float = None, limit: int = None) -> list:
        sql = "SELECT * FROM prospects WHERE 1=1"
        args = []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if role_category:
            sql += " AND role_category = ?"
            args.append(role_category)
        if min_score is not None:
            sql += " AND total_score >= ?"
            args.append(min_score)
        sql += " ORDER BY total_score DESC"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        rows = self.conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["data_json"])
            out.append(d)
        return out

    def get_prospect(self, prospect_id: str) -> dict:
        row = self.conn.execute(
            "SELECT data_json FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        return json.loads(row["data_json"]) if row else None

    def set_status(self, prospect_id: str, status: str, next_action: str = None):
        self.conn.execute(
            "UPDATE prospects SET status = ?, next_action = ? WHERE id = ?",
            (status, next_action, prospect_id))
        row = self.conn.execute(
            "SELECT data_json FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        if row:
            d = json.loads(row["data_json"])
            d["status"] = status
            d["next_action"] = next_action
            self.conn.execute(
                "UPDATE prospects SET data_json = ? WHERE id = ?",
                (json.dumps(d, ensure_ascii=False), prospect_id))
        self.conn.commit()

    # ---- conversations ----
    def save_conversations(self, conversations: list):
        for c in conversations:
            self.conn.execute(
                """INSERT OR IGNORE INTO conversations
                   (prospect_id, conversation_id, participants, topic, last_date,
                    direction, message_count, commercial_signal,
                    relationship_state, follow_up_needed, summary)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (c.get("prospect_id"), c.get("conversation_id"),
                 json.dumps(c.get("participants", [])), c.get("topic"),
                 c.get("last_date"), c.get("direction"), c.get("message_count"),
                 c.get("commercial_signal"), c.get("relationship_state"),
                 1 if c.get("follow_up_needed") else 0, c.get("last_message")))
        self.conn.commit()

    # ---- exclusions ----
    def save_exclusions(self, exclusions: list):
        for e in exclusions:
            self.conn.execute(
                "INSERT INTO exclusions (prospect_id, name, reasons) VALUES (?,?,?)",
                (e.get("prospect_id"), e.get("name"),
                 json.dumps(e.get("reasons", []))))
        self.conn.commit()

    def exclusions(self, prospect_id: str = None) -> list:
        sql = "SELECT * FROM exclusions"
        args = []
        if prospect_id:
            sql += " WHERE prospect_id = ?"
            args.append(prospect_id)
        out = []
        for r in self.conn.execute(sql + " ORDER BY id", args).fetchall():
            d = dict(r)
            try:
                d["reasons"] = json.loads(d.get("reasons") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["reasons"] = []
            out.append(d)
        return out

    # ---- evidence ----
    def add_evidence(self, prospect_id: str, claim: str, observation: str = None,
                     source: str = "", source_type: str = "research",
                     confidence: float = None, collector: str = "research_agent",
                     research_task_id: str = None, created_at: str = None):
        """Store ONE observed claim with full provenance.

        `observation` is the exact visible fact that backs the claim. Inferred
        claims must go through add_inference() instead, which forbids passing
        observation as an observed fact.
        """
        import uuid
        if source_type == "observed" and not observation:
            raise ValueError("observed evidence requires an exact observation")
        self.conn.execute(
            """INSERT INTO evidence
               (evidence_id, prospect_id, claim, observation, source, source_type,
                confidence, collector, research_task_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("ev_" + uuid.uuid4().hex[:10], prospect_id, claim, observation,
             source, source_type, confidence, collector, research_task_id,
             created_at or datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def add_inference(self, prospect_id: str, claim: str,
                      evidence_basis: list = None, confidence: float = None,
                      collector: str = "llm_qualifier", research_task_id: str = None):
        """Store a model inference EXPLICITLY as inference, never as a fact.

        `evidence_basis` lists evidence_ids (or claims) the inference rests on.
        """
        import uuid
        self.conn.execute(
            """INSERT INTO evidence
               (evidence_id, prospect_id, claim, observation, source, source_type,
                confidence, collector, research_task_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("ev_" + uuid.uuid4().hex[:10], prospect_id, claim,
             json.dumps(evidence_basis or []), "llm_reasoning", "inference",
             confidence, collector, research_task_id,
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def evidence_for(self, prospect_id: str) -> list:
        rows = self.conn.execute(
            "SELECT evidence_id, prospect_id, claim, observation, source, "
            "source_type, confidence, collector, research_task_id, created_at "
            "FROM evidence WHERE prospect_id = ? ORDER BY created_at",
            (prospect_id,)).fetchall()
        return [dict(r) for r in rows]

    def observed_evidence_for(self, prospect_id: str) -> list:
        """Only evidence that was actually observed (never inferences)."""
        rows = self.conn.execute(
            "SELECT evidence_id, prospect_id, claim, observation, source, "
            "source_type, confidence, collector, research_task_id, created_at "
            "FROM evidence WHERE prospect_id = ? AND source_type != 'inference' "
            "ORDER BY created_at",
            (prospect_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---- research tasks ----
    def create_research_tasks(self, tasks: list):
        for t in tasks:
            self.conn.execute(
                """INSERT OR REPLACE INTO research_tasks
                   (task_id, prospect_id, objective, required_fields, budget, stage, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (t["task_id"], t["prospect_id"], t["objective"],
                 json.dumps(t["required_fields"]), json.dumps(t["budget"]),
                 t.get("stage", "acquisition"),
                 t.get("status", "pending"), t["created_at"]))
        self.conn.commit()

    def research_tasks(self, status: str = None) -> list:
        sql = "SELECT * FROM research_tasks"
        args = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        rows = self.conn.execute(sql + " ORDER BY rowid", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["required_fields"] = json.loads(d["required_fields"])
            d["budget"] = json.loads(d["budget"])
            out.append(d)
        return out

    def set_task_status(self, task_id: str, status: str):
        self.conn.execute(
            "UPDATE research_tasks SET status = ? WHERE task_id = ?",
            (status, task_id))
        self.conn.commit()

    def get_research_task(self, task_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM research_tasks WHERE task_id = ?",
            (task_id,)).fetchone()
        if not row:
            return None
        return {k: row[k] for k in row.keys()}

    # ---- qualifications ----
    def save_qualification(self, q: dict):
        self.conn.execute(
            """INSERT INTO qualifications
               (prospect_id, fit_score, problem_fit_score, confidence,
                research_mode, method, reason, evidence_used, uncertainties,
                recommended_next_action, contradictions, research_stage, model, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (q["prospect_id"], q.get("fit_score"), q.get("problem_fit_score"),
             q.get("confidence"), q.get("research_mode"), q.get("method"),
             q.get("reason"),
             json.dumps(q.get("evidence_used", [])),
             json.dumps(q.get("uncertainties", [])),
             q.get("recommended_next_action"),
             json.dumps(q.get("contradictions", [])),
             q.get("research_stage", "acquisition"),
             q.get("model"),
             q.get("created_at") or datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def qualifications(self) -> list:
        rows = self.conn.execute(
            "SELECT * FROM qualifications ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("evidence_used", "uncertainties"):
                if isinstance(d.get(k), str):
                    try:
                        d[k] = json.loads(d[k])
                    except (json.JSONDecodeError, TypeError):
                        d[k] = []
            if isinstance(d.get("contradictions"), str):
                try:
                    d["contradictions"] = json.loads(d["contradictions"])
                except (json.JSONDecodeError, TypeError):
                    d["contradictions"] = []
            out.append(d)
        return out

    # ---- human feedback (calibration labels) ----
    def record_feedback(self, prospect_id: str, action: str, reason: str = "",
                        status_before: str = None):
        """Persist ONE human review decision. This is the ground truth used by
        the calibration report (false pos/neg, precision/recall)."""
        self.conn.execute(
            """INSERT INTO human_feedback
               (prospect_id, action, reason, status_before, created_at)
               VALUES (?,?,?,?,?)""",
            (prospect_id, action, reason, status_before,
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def feedback(self, prospect_id: str = None) -> list:
        sql = "SELECT * FROM human_feedback"
        args = []
        if prospect_id:
            sql += " WHERE prospect_id = ?"
            args.append(prospect_id)
        rows = self.conn.execute(sql + " ORDER BY id", args).fetchall()
        return [dict(r) for r in rows]

    # ---- Phase 3: campaigns ----
    def save_campaign(self, campaign: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO campaigns
               (campaign_id, name, objective, strategy, status, config_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (campaign["campaign_id"], campaign.get("name"),
             campaign.get("objective"), campaign.get("strategy"),
             campaign.get("status", "draft"),
             json.dumps(campaign.get("config", {}), ensure_ascii=False),
             campaign.get("created_at") or datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def campaigns(self, status: str = None) -> list:
        sql = "SELECT * FROM campaigns"
        args = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        out = []
        for r in self.conn.execute(sql + " ORDER BY created_at", args).fetchall():
            d = dict(r)
            try:
                d["config"] = json.loads(d.pop("config_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["config"] = {}
            out.append(d)
        return out

    def get_campaign(self, campaign_id: str) -> dict:
        for c in self.campaigns():
            if c["campaign_id"] == campaign_id:
                return c
        return None

    def set_campaign_status(self, campaign_id: str, status: str):
        self.conn.execute("UPDATE campaigns SET status = ? WHERE campaign_id = ?",
                          (status, campaign_id))
        self.conn.commit()

    # ---- campaign prospects (outreach state per prospect per campaign) ----
    def assign_campaign_prospect(self, cp: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO campaign_prospects
               (campaign_id, prospect_id, status, priority, assigned_strategy,
                entered_at, last_action)
               VALUES (?,?,?,?,?,?,?)""",
            (cp["campaign_id"], cp["prospect_id"], cp.get("status", "CAMPAIGN_ASSIGNED"),
             cp.get("priority"), cp.get("assigned_strategy"),
             cp.get("entered_at") or datetime.now(timezone.utc).isoformat(),
             cp.get("last_action")))
        self.conn.commit()

    def campaign_prospects(self, campaign_id: str = None, status: str = None,
                           prospect_id: str = None) -> list:
        sql = "SELECT * FROM campaign_prospects WHERE 1=1"
        args = []
        if campaign_id:
            sql += " AND campaign_id = ?"
            args.append(campaign_id)
        if status:
            sql += " AND status = ?"
            args.append(status)
        if prospect_id:
            sql += " AND prospect_id = ?"
            args.append(prospect_id)
        out = []
        for r in self.conn.execute(sql + " ORDER BY priority DESC", args).fetchall():
            out.append(dict(r))
        return out

    def get_campaign_prospect(self, campaign_id: str, prospect_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM campaign_prospects WHERE campaign_id = ? AND prospect_id = ?",
            (campaign_id, prospect_id)).fetchone()
        return dict(row) if row else None

    def set_campaign_prospect_status(self, campaign_id: str, prospect_id: str,
                                     status: str, note: str = None):
        self.conn.execute(
            "UPDATE campaign_prospects SET status = ?, last_action = ? "
            "WHERE campaign_id = ? AND prospect_id = ?",
            (status, datetime.now(timezone.utc).isoformat(),
             campaign_id, prospect_id))
        self.conn.commit()

    # ---- outreach messages (immutable, versioned) ----
    def save_message(self, msg: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO outreach_messages
               (message_id, prospect_id, campaign_id, strategy, version, text,
                claims, evidence_used, confidence, approved, validation, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (msg["message_id"], msg["prospect_id"], msg["campaign_id"],
             msg.get("strategy"), msg.get("version"), msg.get("text"),
             json.dumps(msg.get("claims", []), ensure_ascii=False),
             json.dumps(msg.get("evidence_used", []), ensure_ascii=False),
             msg.get("confidence"),
             1 if msg.get("approved") else 0,
             json.dumps(msg.get("validation", []), ensure_ascii=False),
             msg.get("status", "pending_review"),
             msg.get("created_at") or datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def messages_for(self, prospect_id: str = None, campaign_id: str = None,
                     status: str = None) -> list:
        sql = "SELECT * FROM outreach_messages WHERE 1=1"
        args = []
        if prospect_id:
            sql += " AND prospect_id = ?"
            args.append(prospect_id)
        if campaign_id:
            sql += " AND campaign_id = ?"
            args.append(campaign_id)
        if status:
            sql += " AND status = ?"
            args.append(status)
        out = []
        for r in self.conn.execute(sql + " ORDER BY created_at", args).fetchall():
            d = dict(r)
            for k in ("claims", "evidence_used", "validation"):
                try:
                    d[k] = json.loads(d[k] or "[]")
                except (json.JSONDecodeError, TypeError):
                    d[k] = []
            d["approved"] = bool(d.get("approved"))
            out.append(d)
        return out

    def set_message_status(self, message_id: str, status: str,
                           approved: bool = None):
        if approved is None:
            self.conn.execute("UPDATE outreach_messages SET status = ? WHERE message_id = ?",
                              (status, message_id))
        else:
            self.conn.execute(
                "UPDATE outreach_messages SET status = ?, approved = ? WHERE message_id = ?",
                (status, 1 if approved else 0, message_id))
        self.conn.commit()

    def get_message(self, message_id: str) -> dict:
        for m in self.messages_for():
            if m["message_id"] == message_id:
                return m
        return None

    # ---- outreach state events (audit log) ----
    def log_outreach_event(self, prospect_id: str, campaign_id: str,
                           from_state: str, to_state: str, event: str,
                           note: str = None):
        self.conn.execute(
            """INSERT INTO outreach_events
               (prospect_id, campaign_id, from_state, to_state, event, note, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (prospect_id, campaign_id, from_state, to_state, event, note,
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def outreach_events(self, prospect_id: str = None,
                        campaign_id: str = None) -> list:
        sql = "SELECT * FROM outreach_events WHERE 1=1"
        args = []
        if prospect_id:
            sql += " AND prospect_id = ?"
            args.append(prospect_id)
        if campaign_id:
            sql += " AND campaign_id = ?"
            args.append(campaign_id)
        rows = self.conn.execute(sql + " ORDER BY id", args).fetchall()
        return [dict(r) for r in rows]

    # ---- outreach conversations (classified replies) ----
    def save_conversation(self, c: dict):
        self.conn.execute(
            """INSERT INTO outreach_conversations
               (prospect_id, campaign_id, intent, sentiment, pain_signal,
                commercial_intent, objection, confidence, raw, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (c["prospect_id"], c.get("campaign_id"), c.get("intent"),
             c.get("sentiment"), c.get("pain_signal"),
             c.get("commercial_intent"), c.get("objection"),
             c.get("confidence"),
             json.dumps(c.get("raw", ""), ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def conversations_outreach(self, prospect_id: str = None) -> list:
        sql = "SELECT * FROM outreach_conversations"
        args = []
        if prospect_id:
            sql += " WHERE prospect_id = ?"
            args.append(prospect_id)
        rows = self.conn.execute(sql + " ORDER BY id", args).fetchall()
        return [dict(r) for r in rows]

    # ---- product events (website attribution chain) ----
    def record_product_event(self, prospect_id: str, event_name: str,
                             campaign_id: str = None, message_id: str = None,
                             meta: dict = None):
        self.conn.execute(
            """INSERT INTO product_events
               (prospect_id, campaign_id, message_id, event_name, meta_json, created_at)
               VALUES (?,?,?,?,?,?)""",
            (prospect_id, campaign_id, message_id, event_name,
             json.dumps(meta or {}, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def product_events(self, prospect_id: str = None,
                       event_name: str = None) -> list:
        sql = "SELECT * FROM product_events WHERE 1=1"
        args = []
        if prospect_id:
            sql += " AND prospect_id = ?"
            args.append(prospect_id)
        if event_name:
            sql += " AND event_name = ?"
            args.append(event_name)
        rows = self.conn.execute(sql + " ORDER BY id", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["meta"] = json.loads(d.pop("meta_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["meta"] = {}
            out.append(d)
        return out

    # ---- Phase 4: experiments ----
    def save_experiment(self, exp: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO experiments
               (experiment_id, hypothesis, control, variant, segment,
                primary_metric, minimum_sample, status, result_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (exp["experiment_id"], exp.get("hypothesis"), exp.get("control"),
             exp.get("variant"), exp.get("segment"), exp.get("primary_metric"),
             exp.get("minimum_sample"), exp.get("status", "running"),
             json.dumps(exp.get("result", {}), ensure_ascii=False),
             exp.get("created_at") or datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def experiments(self, status: str = None) -> list:
        sql = "SELECT * FROM experiments"
        args = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        out = []
        for r in self.conn.execute(sql + " ORDER BY created_at", args).fetchall():
            d = dict(r)
            try:
                d["result"] = json.loads(d.pop("result_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["result"] = {}
            out.append(d)
        return out

    def get_experiment(self, experiment_id: str) -> dict:
        for e in self.experiments():
            if e["experiment_id"] == experiment_id:
                return e
        return None

    def set_experiment_result(self, experiment_id: str, result: dict,
                              status: str = None):
        exp = self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"no experiment {experiment_id}")
        merged = dict(exp.get("result") or {})
        merged.update(result)
        new_status = status or exp.get("status", "running")
        self.conn.execute(
            "UPDATE experiments SET result_json = ?, status = ? "
            "WHERE experiment_id = ?",
            (json.dumps(merged, ensure_ascii=False), new_status, experiment_id))
        self.conn.commit()

    # ---- segments ----
    def save_segment(self, name: str, description: str, prospects: list):
        ids = [p["prospect_id"] if isinstance(p, dict) else p for p in prospects]
        self.conn.execute(
            "INSERT INTO segments (name, description, prospects, created_at) "
            "VALUES (?,?,?,?)",
            (name, description, json.dumps(ids),
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def stats(self) -> dict:
        def one(sql):
            return self.conn.execute(sql).fetchone()[0]
        return {
            "prospects": one("SELECT COUNT(*) FROM prospects"),
            "conversations": one("SELECT COUNT(*) FROM conversations"),
            "exclusions": one("SELECT COUNT(*) FROM exclusions"),
            "evidence": one("SELECT COUNT(*) FROM evidence"),
            "research_tasks": one("SELECT COUNT(*) FROM research_tasks"),
            "campaigns": one("SELECT COUNT(*) FROM campaigns"),
            "campaign_prospects": one("SELECT COUNT(*) FROM campaign_prospects"),
            "outreach_messages": one("SELECT COUNT(*) FROM outreach_messages"),
            "outreach_events": one("SELECT COUNT(*) FROM outreach_events"),
            "outreach_conversations": one("SELECT COUNT(*) FROM outreach_conversations"),
            "product_events": one("SELECT COUNT(*) FROM product_events"),
            "experiments": one("SELECT COUNT(*) FROM experiments"),
        }
