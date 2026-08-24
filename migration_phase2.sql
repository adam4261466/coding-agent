-- Phase 2 durability migration.
-- Safe to run multiple times. Does NOT delete WAL/SHM files.

PRAGMA foreign_keys=ON;

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