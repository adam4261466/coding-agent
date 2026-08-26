"""Small, explicit LinkedIn conversation assistant.

This is intentionally NOT an autonomous LinkedIn browser agent.
It only:
  1. finds one prospect in the local DB,
  2. opens that prospect's LinkedIn URL,
  3. asks Ollama for a tailored draft,
  4. lets the user send it manually,
  5. stores the real outbound/inbound conversation.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import urllib.request
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_agent.db")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:31b-cloud")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS prospects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                email TEXT,
                company TEXT,
                position TEXT,
                connected_on TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('outbound','inbound')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('initial','reply')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_prospects_name
                ON prospects(first_name, last_name);
            CREATE INDEX IF NOT EXISTS idx_messages_prospect
                ON messages(prospect_id, created_at);
            """
        )


def reset_db() -> None:
    with connect() as db:
        db.execute("DROP TABLE IF EXISTS drafts")
        db.execute("DROP TABLE IF EXISTS messages")
        db.execute("DROP TABLE IF EXISTS prospects")
    init_db()


def upsert_prospect(row: dict[str, Any]) -> None:
    now = utc_now()
    with connect() as db:
        db.execute(
            """
            INSERT INTO prospects
              (first_name,last_name,url,email,company,position,connected_on,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
              first_name=excluded.first_name,
              last_name=excluded.last_name,
              email=excluded.email,
              company=excluded.company,
              position=excluded.position,
              connected_on=excluded.connected_on,
              updated_at=excluded.updated_at
            """,
            (
                row["First Name"].strip(),
                row["Last Name"].strip(),
                row["URL"].strip(),
                row.get("Email Address", "").strip(),
                row.get("Company", "").strip(),
                row.get("Position", "").strip(),
                row.get("Connected On", "").strip(),
                now,
                now,
            ),
        )


def search_prospects(query: str = "", limit: int = 50) -> list[sqlite3.Row]:
    q = f"%{query.strip()}%"
    with connect() as db:
        return db.execute(
            """
            SELECT * FROM prospects
            WHERE ? = '%%'
               OR first_name LIKE ? COLLATE NOCASE
               OR last_name LIKE ? COLLATE NOCASE
               OR (first_name || ' ' || last_name) LIKE ? COLLATE NOCASE
               OR company LIKE ? COLLATE NOCASE
            ORDER BY last_name, first_name
            LIMIT ?
            """,
            (q, q, q, q, q, limit),
        ).fetchall()


def get_prospect(prospect_id: int) -> sqlite3.Row | None:
    with connect() as db:
        return db.execute("SELECT * FROM prospects WHERE id=?", (prospect_id,)).fetchone()


def get_messages(prospect_id: int) -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute(
            "SELECT * FROM messages WHERE prospect_id=? ORDER BY created_at, id",
            (prospect_id,),
        ).fetchall()


def add_message(prospect_id: int, direction: str, content: str) -> None:
    content = content.strip()
    if not content:
        raise ValueError("Message cannot be empty")
    if direction not in {"outbound", "inbound"}:
        raise ValueError("direction must be outbound or inbound")
    with connect() as db:
        db.execute(
            "INSERT INTO messages(prospect_id,direction,content,created_at) VALUES(?,?,?,?)",
            (prospect_id, direction, content, utc_now()),
        )


def save_draft(prospect_id: int, kind: str, content: str) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO drafts(prospect_id,kind,content,created_at) VALUES(?,?,?,?)",
            (prospect_id, kind, content.strip(), utc_now()),
        )


def format_history(prospect_id: int, max_messages: int = 20) -> str:
    rows = get_messages(prospect_id)[-max_messages:]
    if not rows:
        return "No real conversation has been sent yet."
    return "\n".join(
        f"{row['direction'].upper()}: {row['content']}" for row in rows
    )


def open_profile(url: str) -> None:
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    else:
        import webbrowser
        webbrowser.open(url)


def _ollama_chat(messages: list[dict[str, str]], temperature: float = 0.65) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": 12000},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not reach Ollama at {OLLAMA_URL}: {exc}") from exc
    text = data.get("message", {}).get("content", "").strip()
    if not text:
        raise RuntimeError("Ollama returned an empty message")
    return text


def _prospect_context(prospect: sqlite3.Row) -> str:
    return (
        f"Name: {prospect['first_name']} {prospect['last_name']}\n"
        f"LinkedIn: {prospect['url']}\n"
        f"Company: {prospect['company'] or 'Unknown'}\n"
        f"Position: {prospect['position'] or 'Unknown'}\n"
        f"Connected on: {prospect['connected_on'] or 'Unknown'}\n"
        f"Email: {prospect['email'] or 'Not available'}"
    )


def generate_initial(prospect_id: int) -> str:
    prospect = get_prospect(prospect_id)
    if not prospect:
        raise ValueError("Prospect not found")
    history = format_history(prospect_id)
    prompt = (
        "Write ONE natural LinkedIn message to this existing connection. "
        "It must be genuinely specific to the person's role/company, avoid fake claims, "
        "avoid mentioning hidden research, and avoid generic sales language. "
        "Keep it concise (60-110 words), human and conversational. Do not include a subject. "
        "The goal is to start a real conversation, not hard-sell.\n\n"
        f"PROSPECT\n{_prospect_context(prospect)}\n\n"
        f"EXISTING CONVERSATION\n{history}"
    )
    draft = _ollama_chat(
        [
            {"role": "system", "content": "You are an excellent one-to-one LinkedIn outreach writer."},
            {"role": "user", "content": prompt},
        ]
    )
    save_draft(prospect_id, "initial", draft)
    return draft


def generate_reply(prospect_id: int) -> str:
    prospect = get_prospect(prospect_id)
    if not prospect:
        raise ValueError("Prospect not found")
    history = format_history(prospect_id, max_messages=30)
    prompt = (
        "Write the next LinkedIn reply in an ongoing one-to-one conversation. "
        "Reply directly to the prospect's latest inbound message, respect their tone, "
        "use the known prospect context, and move the conversation forward naturally. "
        "Never invent facts. Keep it concise (40-100 words). Do not add commentary or quotes.\n\n"
        f"PROSPECT\n{_prospect_context(prospect)}\n\n"
        f"CONVERSATION\n{history}"
    )
    draft = _ollama_chat(
        [
            {"role": "system", "content": "You are a thoughtful LinkedIn conversation assistant."},
            {"role": "user", "content": prompt},
        ]
    )
    save_draft(prospect_id, "reply", draft)
    return draft


init_db()
