"""Durable Phase 2 workflow state and checkpoint persistence.

This module is deliberately independent of the UI. Every meaningful transition
is committed immediately so a process restart resumes from the last durable step.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from linkedin_intelligence.db import connect

from linkedin_intelligence.db import connect


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


class WorkflowStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn = connect(self.db_path)
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def _ensure_schema(self) -> None:
        # All schema creation is idempotent. This lets Phase 2 start safely on an
        # existing database without deleting or rebuilding user data.
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow_tasks (
                task_id TEXT PRIMARY KEY,
                prospect_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step INTEGER NOT NULL DEFAULT 0,
                total_steps INTEGER NOT NULL DEFAULT 0,
                current_step_key TEXT,
                last_tool TEXT,
                last_tool_result TEXT,
                checkpoint TEXT,
                error TEXT,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_tasks_prospect_stage
              ON workflow_tasks(prospect_id, stage);

            CREATE TABLE IF NOT EXISTS workflow_steps (
                step_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                prospect_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                step_key TEXT NOT NULL,
                status TEXT NOT NULL,
                tool TEXT,
                input_json TEXT,
                result_json TEXT,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES workflow_tasks(task_id) ON DELETE CASCADE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_steps_task_key
              ON workflow_steps(task_id, step_key);

            CREATE TABLE IF NOT EXISTS prospect_evidence (
                evidence_id TEXT PRIMARY KEY,
                prospect_id TEXT NOT NULL,
                source TEXT NOT NULL,
                evidence TEXT NOT NULL,
                url TEXT,
                source_type TEXT,
                captured_at TEXT NOT NULL,
                metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS prospect_qualifications (
                qualification_id TEXT PRIMARY KEY,
                prospect_id TEXT NOT NULL UNIQUE,
                fit_score REAL,
                confidence REAL,
                method TEXT,
                rationale TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prospect_segments (
                segment_id TEXT PRIMARY KEY,
                prospect_id TEXT NOT NULL UNIQUE,
                segment TEXT NOT NULL,
                confidence REAL,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prospect_actions (
                action_id TEXT PRIMARY KEY,
                prospect_id TEXT NOT NULL UNIQUE,
                next_action TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prospect_events (
                event_id TEXT PRIMARY KEY,
                prospect_id TEXT NOT NULL,
                task_id TEXT,
                event_type TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS ix_prospect_events_prospect
              ON prospect_events(prospect_id, created_at);

            CREATE INDEX IF NOT EXISTS ix_workflow_tasks_status
              ON workflow_tasks(status, stage);
            """
        )
        self.conn.commit()

    def _event(self, prospect_id: str, event_type: str, stage: str,
               status: Optional[str] = None, task_id: Optional[str] = None,
               payload: Any = None) -> None:
        self.conn.execute(
            """INSERT INTO prospect_events
               (event_id, prospect_id, task_id, event_type, stage, status, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), prospect_id, task_id, event_type, stage, status,
             json_dumps(payload) if payload is not None else None, utc_now()),
        )

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def get_task(self, prospect_id: str, stage: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM workflow_tasks WHERE prospect_id=? AND stage=?",
            (prospect_id, stage),
        ).fetchone()

    def ensure_task(self, prospect_id: str, stage: str, total_steps: int,
                    step_keys: list[str]) -> sqlite3.Row:
        now = utc_now()
        existing = self.get_task(prospect_id, stage)
        if existing:
            self.conn.execute(
                "UPDATE workflow_tasks SET total_steps=?, updated_at=? WHERE task_id=?",
                (total_steps, now, existing["task_id"]),
            )
            for index, key in enumerate(step_keys, start=1):
                self.conn.execute(
                    """INSERT OR IGNORE INTO workflow_steps
                       (step_id, task_id, prospect_id, stage, step_index, step_key,
                        status, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (str(uuid.uuid4()), existing["task_id"], prospect_id, stage,
                     index, key, now),
                )
            self.conn.commit()
            return self.get_task(prospect_id, stage)  # type: ignore[return-value]

        task_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO workflow_tasks
               (task_id, prospect_id, stage, status, current_step, total_steps,
                updated_at)
               VALUES (?, ?, ?, 'pending', 0, ?, ?)""",
            (task_id, prospect_id, stage, total_steps, now),
        )
        for index, key in enumerate(step_keys, start=1):
            self.conn.execute(
                """INSERT INTO workflow_steps
                   (step_id, task_id, prospect_id, stage, step_index, step_key,
                    status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (str(uuid.uuid4()), task_id, prospect_id, stage, index, key, now),
            )
        self._event(prospect_id, "task_created", stage, "pending", task_id,
                    {"total_steps": total_steps})
        self.conn.commit()
        return self.get_task(prospect_id, stage)  # type: ignore[return-value]

    def start_task(self, prospect_id: str, stage: str, task_id: str) -> None:
        now = utc_now()
        self.conn.execute(
            """UPDATE workflow_tasks
               SET status='running', started_at=COALESCE(started_at, ?), updated_at=?, error=NULL
               WHERE task_id=?""",
            (now, now, task_id),
        )
        self._event(prospect_id, f"{stage}_started", stage, "running", task_id)
        self.conn.commit()

    def recover_running_tasks(self) -> None:
        """Turn stale running tasks back into resumable pending tasks.

        A crash leaves durable step state. A restart should never interpret
        'running' as 'start from zero'.
        """
        self.conn.execute(
            "UPDATE workflow_tasks SET status='pending', updated_at=? WHERE status='running'",
            (utc_now(),),
        )
        self.conn.execute(
            """UPDATE workflow_steps
               SET status='pending', updated_at=?
               WHERE status='running'""",
            (utc_now(),),
        )
        self.conn.commit()

    def get_step(self, task_id: str, step_key: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM workflow_steps WHERE task_id=? AND step_key=?",
            (task_id, step_key),
        ).fetchone()

    def begin_step(self, task_id: str, prospect_id: str, stage: str,
                   step_index: int, step_key: str, tool: Optional[str] = None,
                   input_data: Any = None) -> None:
        now = utc_now()
        self.conn.execute(
            """UPDATE workflow_steps
               SET status='running', tool=?, input_json=?, started_at=COALESCE(started_at, ?), updated_at=?
               WHERE task_id=? AND step_key=?""",
            (tool, json_dumps(input_data) if input_data is not None else None,
             now, now, task_id, step_key),
        )
        self.conn.execute(
            """UPDATE workflow_tasks
               SET current_step=?, current_step_key=?, last_tool=?, updated_at=?
               WHERE task_id=?""",
            (step_index, step_key, tool, now, task_id),
        )
        self._event(prospect_id, "step_started", stage, "running", task_id,
                    {"step_index": step_index, "step_key": step_key, "tool": tool})
        self.conn.commit()

    def complete_step(self, task_id: str, prospect_id: str, stage: str,
                      step_index: int, step_key: str, result: Any = None,
                      tool: Optional[str] = None) -> None:
        now = utc_now()
        self.conn.execute(
            """UPDATE workflow_steps
               SET status='completed', result_json=?, tool=COALESCE(?, tool),
                   completed_at=?, updated_at=?
               WHERE task_id=? AND step_key=?""",
            (json_dumps(result) if result is not None else None,
             tool, now, now, task_id, step_key),
        )
        self.conn.execute(
            """UPDATE workflow_tasks
               SET current_step=?, current_step_key=?, last_tool=?,
                   last_tool_result=?, checkpoint=?, updated_at=?
               WHERE task_id=?""",
            (step_index, step_key, tool,
             json_dumps(result) if result is not None else None,
             json_dumps({"step_index": step_index, "step_key": step_key,
                         "completed_at": now}),
             now, task_id),
        )
        self._event(prospect_id, "step_completed", stage, "completed", task_id,
                    {"step_index": step_index, "step_key": step_key, "result": result})
        self.conn.commit()

    def fail_step(self, task_id: str, prospect_id: str, stage: str,
                  step_key: str, error: Exception) -> None:
        now = utc_now()
        message = f"{type(error).__name__}: {error}"
        self.conn.execute(
            "UPDATE workflow_steps SET status='pending', updated_at=? WHERE task_id=? AND step_key=?",
            (now, task_id, step_key),
        )
        self.conn.execute(
            "UPDATE workflow_tasks SET status='pending', error=?, updated_at=? WHERE task_id=?",
            (message, now, task_id),
        )
        self._event(prospect_id, "step_failed", stage, "pending", task_id,
                    {"step_key": step_key, "error": message})
        self.conn.commit()

    def finish_task(self, task_id: str, prospect_id: str, stage: str) -> None:
        now = utc_now()
        self.conn.execute(
            """UPDATE workflow_tasks
               SET status='completed', completed_at=?, updated_at=?, error=NULL
               WHERE task_id=?""",
            (now, now, task_id),
        )
        self._event(prospect_id, f"{stage}_completed", stage, "completed", task_id)
        self.conn.commit()

    def save_evidence(self, prospect_id: str, source: str, evidence: str,
                      url: Optional[str] = None,
                      source_type: Optional[str] = None,
                      metadata: Any = None) -> str:
        evidence_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO prospect_evidence
               (evidence_id, prospect_id, source, evidence, url, source_type, captured_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (evidence_id, prospect_id, source, evidence, url, source_type,
             utc_now(), json_dumps(metadata) if metadata is not None else None),
        )
        self._event(prospect_id, "evidence_captured", "research", "completed",
                    payload={"evidence_id": evidence_id, "source": source, "url": url})
        self.conn.commit()
        return evidence_id

    def save_qualification(self, prospect_id: str, fit_score: Optional[float],
                           confidence: Optional[float], method: str,
                           rationale: Optional[str], payload: Any = None) -> None:
        now = utc_now()
        self.conn.execute(
            """INSERT INTO prospect_qualifications
               (qualification_id, prospect_id, fit_score, confidence, method,
                rationale, payload_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(prospect_id) DO UPDATE SET
                 fit_score=excluded.fit_score, confidence=excluded.confidence,
                 method=excluded.method, rationale=excluded.rationale,
                 payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
            (str(uuid.uuid4()), prospect_id, fit_score, confidence, method,
             rationale, json_dumps(payload) if payload is not None else None,
             now, now),
        )
        self._event(prospect_id, "qualification_completed", "qualification", "completed",
                    payload={"fit_score": fit_score, "confidence": confidence, "method": method})
        self.conn.commit()

    def save_segment(self, prospect_id: str, segment: str,
                     confidence: Optional[float] = None, payload: Any = None) -> None:
        now = utc_now()
        self.conn.execute(
            """INSERT INTO prospect_segments
               (segment_id, prospect_id, segment, confidence, payload_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(prospect_id) DO UPDATE SET
                 segment=excluded.segment, confidence=excluded.confidence,
                 payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
            (str(uuid.uuid4()), prospect_id, segment, confidence,
             json_dumps(payload) if payload is not None else None, now, now),
        )
        self._event(prospect_id, "segment_assigned", "segmentation", "completed",
                    payload={"segment": segment, "confidence": confidence})
        self.conn.commit()

    def save_next_action(self, prospect_id: str, next_action: str,
                         payload: Any = None) -> None:
        now = utc_now()
        self.conn.execute(
            """INSERT INTO prospect_actions
               (action_id, prospect_id, next_action, payload_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(prospect_id) DO UPDATE SET
                 next_action=excluded.next_action,
                 payload_json=excluded.payload_json,
                 updated_at=excluded.updated_at""",
            (str(uuid.uuid4()), prospect_id, next_action,
             json_dumps(payload) if payload is not None else None, now, now),
        )
        self._event(prospect_id, "next_action_assigned", "next_action", "completed",
                    payload={"next_action": next_action})
        self.conn.commit()