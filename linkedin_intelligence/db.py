"""Shared SQLite connection helper.

Every store in this package (EventStore, StateStore, FactStore, TaskStore,
SnapshotStore, and the main Store) opens its own `sqlite3.connect(...)` to
the same file, and every write path calls `.commit()` immediately. Without
WAL mode, SQLite's default rollback-journal mode takes an exclusive lock
for the duration of each write - with five independent connections in one
process (more once BrowserExecutor / research agents add their own), you
will eventually see "database is locked" under real load, usually the
first time two cycles overlap.

connect() gives every store the same safe defaults:
  - WAL journal mode: readers don't block writers, writers don't block
    readers. This is the single biggest lock-contention fix available
    without restructuring how the stores share a connection.
  - busy_timeout: a writer that finds the db locked waits (up to the
    timeout) instead of raising sqlite3.OperationalError immediately.
  - synchronous=NORMAL: safe under WAL (durable across app crashes,
    fsyncs less aggressively than FULL) - the standard pairing with WAL.

Usage: replace `sqlite3.connect(db_path, check_same_thread=False)` with
`connect(db_path)` in every store's __init__. Nothing else changes - same
Connection object, same row_factory, same API.
"""

import sqlite3


def connect(db_path: str, timeout: float = 10.0) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
