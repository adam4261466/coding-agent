"""Task store: objectives and progress per prospect.

Tasks represent what the agent is trying to accomplish. Each task tracks:
  - The goal (e.g., convert_prospect, activate_user)
  - What has been completed
  - What is pending
  - What failed or is blocked
  - The next action to take

When the agent restarts, it reads its tasks and continues where it left off.

TASK STATUSES:
  active    — currently working on this
  paused    — waiting for external input (human approval, prospect reply)
  completed — goal achieved
  failed    — goal cannot be achieved
"""


from ...timeutil import iso as _now_iso
import json
import sqlite3
from datetime import datetime, timezone

from ...db import connect as db_connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    prospect_id TEXT NOT NULL,
    campaign_id TEXT,
    goal TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    completed_steps TEXT,
    pending_steps TEXT,
    failed_steps TEXT,
    blocked_steps TEXT,
    next_action_json TEXT,
    result_json TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_prospect ON tasks(prospect_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


class TaskStore:
    """Objectives and progress. Enables crash recovery and continuity."""

    def __init__(self, db_path: str):
        self.conn = db_connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def create_task(self, prospect_id: str, goal: str,
                    campaign_id: str = None,
                    initial_steps: list = None) -> dict:
        """Create a new task. Returns the task dict."""
        import uuid
        now = _now_iso()
        task_id = "task_" + uuid.uuid4().hex[:10]
        task = {
            "task_id": task_id,
            "prospect_id": prospect_id,
            "campaign_id": campaign_id,
            "goal": goal,
            "status": "active",
            "completed": [],
            "pending": list(initial_steps or []),
            "failed": [],
            "blocked": [],
            "next_action": None,
            "result": {},
            "created_at": now,
            "updated_at": now,
        }
        self._save_task(task)
        return task

    def get_task(self, task_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._load_task(row) if row else None

    def tasks_for(self, prospect_id: str = None, status: str = None,
                  goal: str = None) -> list:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args = []
        if prospect_id:
            sql += " AND prospect_id = ?"
            args.append(prospect_id)
        if status:
            sql += " AND status = ?"
            args.append(status)
        if goal:
            sql += " AND goal = ?"
            args.append(goal)
        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, args).fetchall()
        return [self._load_task(r) for r in rows]

    def active_tasks(self, prospect_id: str = None) -> list:
        """Get all active tasks. This is what the agent reads on restart."""
        return self.tasks_for(prospect_id=prospect_id, status="active")

    def complete_step(self, task_id: str, step: str):
        task = self.get_task(task_id)
        if not task:
            return
        if step in task["pending"]:
            task["pending"].remove(step)
        if step not in task["completed"]:
            task["completed"].append(step)
        task["updated_at"] = _now_iso()
        self._save_task(task)

    def fail_step(self, task_id: str, step: str, reason: str = None):
        task = self.get_task(task_id)
        if not task:
            return
        if step in task["pending"]:
            task["pending"].remove(step)
        entry = {"step": step, "reason": reason}
        if entry not in task["failed"]:
            task["failed"].append(entry)
        task["updated_at"] = _now_iso()
        self._save_task(task)

    def block_step(self, task_id: str, step: str, reason: str = None):
        task = self.get_task(task_id)
        if not task:
            return
        if step in task["pending"]:
            task["pending"].remove(step)
        entry = {"step": step, "reason": reason}
        if entry not in task["blocked"]:
            task["blocked"].append(entry)
        task["updated_at"] = _now_iso()
        self._save_task(task)

    def set_next_action(self, task_id: str, action_type: str,
                        reason: str = None, params: dict = None):
        task = self.get_task(task_id)
        if not task:
            return
        task["next_action"] = {
            "type": action_type,
            "reason": reason,
            "params": params or {},
        }
        task["updated_at"] = _now_iso()
        self._save_task(task)

    def complete_task(self, task_id: str, result: dict = None):
        task = self.get_task(task_id)
        if not task:
            return
        task["status"] = "completed"
        task["result"] = result or {}
        task["updated_at"] = _now_iso()
        self._save_task(task)

    def fail_task(self, task_id: str, reason: str = None):
        task = self.get_task(task_id)
        if not task:
            return
        task["status"] = "failed"
        task["result"] = {"reason": reason}
        task["updated_at"] = _now_iso()
        self._save_task(task)

    def progress(self, task_id: str) -> dict:
        """Return a compact progress summary."""
        task = self.get_task(task_id)
        if not task:
            return {}
        total = len(task["completed"]) + len(task["pending"]) + \
                len(task["failed"]) + len(task["blocked"])
        return {
            "task_id": task_id,
            "goal": task["goal"],
            "status": task["status"],
            "completed": len(task["completed"]),
            "pending": len(task["pending"]),
            "failed": len(task["failed"]),
            "blocked": len(task["blocked"]),
            "total": total,
            "next_action": task.get("next_action"),
        }

    def _save_task(self, task: dict):
        now = _now_iso()
        task["updated_at"] = now
        self.conn.execute(
            """INSERT OR REPLACE INTO tasks
               (task_id, prospect_id, campaign_id, goal, status,
                completed_steps, pending_steps, failed_steps, blocked_steps,
                next_action_json, result_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task["task_id"], task["prospect_id"], task.get("campaign_id"),
             task["goal"], task["status"],
             json.dumps(task["completed"], ensure_ascii=False),
             json.dumps(task["pending"], ensure_ascii=False),
             json.dumps(task["failed"], ensure_ascii=False),
             json.dumps(task["blocked"], ensure_ascii=False),
             json.dumps(task.get("next_action") or {}, ensure_ascii=False),
             json.dumps(task.get("result") or {}, ensure_ascii=False),
             task["created_at"], task["updated_at"]))
        self.conn.commit()

    def _load_task(self, row) -> dict:
        d = dict(row)
        for k in ("completed_steps", "pending_steps", "failed_steps",
                   "blocked_steps"):
            try:
                d[k.replace("_steps", "")] = json.loads(d.pop(k) or "[]")
            except (json.JSONDecodeError, TypeError):
                d[k.replace("_steps", "")] = []
        try:
            d["next_action"] = json.loads(d.pop("next_action_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["next_action"] = {}
        try:
            d["result"] = json.loads(d.pop("result_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["result"] = {}
        return d
