"""Simple LinkedIn prospect and conversation assistant."""
from __future__ import annotations

import json
import os
import re
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
        db.executescript("""
        CREATE TABLE IF NOT EXISTS prospects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            email TEXT,
            company TEXT,
            position TEXT,
            connected_on TEXT,
            eliminated INTEGER NOT NULL DEFAULT 0,
            elimination_reason TEXT,
            eliminated_at TEXT,
            relationship TEXT NOT NULL DEFAULT 'connected',
            source TEXT NOT NULL DEFAULT 'connections_csv',
            profile_text TEXT,
            profile_title TEXT,
            profile_headline TEXT,
            profile_location TEXT,
            profile_fetched_at TEXT,
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
        CREATE INDEX IF NOT EXISTS idx_prospects_name ON prospects(first_name,last_name);
        CREATE INDEX IF NOT EXISTS idx_messages_prospect ON messages(prospect_id,created_at);
        """)
        existing = {r[1] for r in db.execute("PRAGMA table_info(prospects)")}
        migrations = {
            "eliminated": "ALTER TABLE prospects ADD COLUMN eliminated INTEGER NOT NULL DEFAULT 0",
            "elimination_reason": "ALTER TABLE prospects ADD COLUMN elimination_reason TEXT",
            "eliminated_at": "ALTER TABLE prospects ADD COLUMN eliminated_at TEXT",
            "relationship": "ALTER TABLE prospects ADD COLUMN relationship TEXT NOT NULL DEFAULT 'connected'",
            "source": "ALTER TABLE prospects ADD COLUMN source TEXT NOT NULL DEFAULT 'connections_csv'",
            "profile_text": "ALTER TABLE prospects ADD COLUMN profile_text TEXT",
            "profile_title": "ALTER TABLE prospects ADD COLUMN profile_title TEXT",
            "profile_headline": "ALTER TABLE prospects ADD COLUMN profile_headline TEXT",
            "profile_location": "ALTER TABLE prospects ADD COLUMN profile_location TEXT",
            "profile_fetched_at": "ALTER TABLE prospects ADD COLUMN profile_fetched_at TEXT",
        }
        for col, sql in migrations.items():
            if col not in existing:
                db.execute(sql)


def reset_db() -> None:
    with connect() as db:
        db.execute("DROP TABLE IF EXISTS drafts")
        db.execute("DROP TABLE IF EXISTS messages")
        db.execute("DROP TABLE IF EXISTS prospects")
    init_db()


def upsert_prospect(row: dict[str, Any]) -> None:
    now = utc_now()
    with connect() as db:
        db.execute("""
            INSERT INTO prospects
              (first_name,last_name,url,email,company,position,connected_on,relationship,source,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
              first_name=excluded.first_name,last_name=excluded.last_name,email=excluded.email,
              company=excluded.company,position=excluded.position,connected_on=excluded.connected_on,
              updated_at=excluded.updated_at
        """, (
            (row.get("First Name") or "").strip(), (row.get("Last Name") or "").strip(),
            (row.get("URL") or "").strip(), (row.get("Email Address") or "").strip(),
            (row.get("Company") or "").strip(), (row.get("Position") or "").strip(),
            (row.get("Connected On") or "").strip(), "connected", "connections_csv", now, now))


def _name_from_url(url: str) -> tuple[str, str]:
    slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"-[a-z0-9]{6,}$", "", slug, flags=re.I)
    parts = [p for p in re.split(r"[-_]+", slug) if p]
    return (parts[0].title(), " ".join(parts[1:]).title()) if len(parts) > 1 else ((parts[0].title(), "") if parts else ("Unknown", "Prospect"))


def add_direct_profile(profile: dict[str, str]) -> int:
    first, last = _name_from_url(profile["url"])
    actual = (profile.get("name") or "").strip()
    if actual:
        bits = actual.split(); first, last = bits[0], " ".join(bits[1:])
    now = utc_now()
    with connect() as db:
        found = db.execute("SELECT id, relationship, source FROM prospects WHERE url=?", (profile["url"],)).fetchone()
        if found:
            db.execute("""UPDATE prospects SET first_name=?,last_name=?,profile_text=?,profile_title=?,profile_headline=?,profile_location=?,profile_fetched_at=?,updated_at=? WHERE id=?""",
                       (first,last,profile.get("profile_text"),profile.get("title"),profile.get("headline"),profile.get("location"),now,now,found["id"]))
            return int(found["id"])
        cur = db.execute("""INSERT INTO prospects
            (first_name,last_name,url,company,position,relationship,source,profile_text,profile_title,profile_headline,profile_location,profile_fetched_at,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (first,last,profile["url"],"",profile.get("headline") or "","not_connected","direct_url",
             profile.get("profile_text"),profile.get("title"),profile.get("headline"),profile.get("location"),now,now,now))
        return int(cur.lastrowid)


def search_prospects(query: str = "", limit: int = 1000) -> list[sqlite3.Row]:
    q = f"%{query.strip()}%"
    with connect() as db:
        return db.execute("""SELECT * FROM prospects
            WHERE (? = '%%' OR first_name LIKE ? COLLATE NOCASE OR last_name LIKE ? COLLATE NOCASE
                   OR (first_name || ' ' || last_name) LIKE ? COLLATE NOCASE OR company LIKE ? COLLATE NOCASE)
            ORDER BY last_name,first_name LIMIT ?""", (q,q,q,q,q,limit)).fetchall()


def get_prospect(prospect_id: int):
    with connect() as db:
        return db.execute("SELECT * FROM prospects WHERE id=?", (prospect_id,)).fetchone()


def get_messages(prospect_id: int):
    with connect() as db:
        return db.execute("SELECT * FROM messages WHERE prospect_id=? ORDER BY created_at,id", (prospect_id,)).fetchall()


def add_message(prospect_id: int, direction: str, content: str) -> None:
    content = content.strip()
    if not content or direction not in {"outbound","inbound"}:
        raise ValueError("Message must not be empty and direction must be outbound/inbound")
    with connect() as db:
        db.execute("INSERT INTO messages(prospect_id,direction,content,created_at) VALUES(?,?,?,?)", (prospect_id,direction,content,utc_now()))


def save_draft(prospect_id: int, kind: str, content: str) -> None:
    with connect() as db:
        db.execute("INSERT INTO drafts(prospect_id,kind,content,created_at) VALUES(?,?,?,?)", (prospect_id,kind,content.strip(),utc_now()))


def format_history(prospect_id: int, max_messages: int = 30) -> str:
    rows = get_messages(prospect_id)[-max_messages:]
    return "\n".join(f"{r['direction'].upper()}: {r['content']}" for r in rows) if rows else "No real conversation yet."


def open_profile(url: str) -> None:
    if os.name == "nt": subprocess.Popen(["cmd","/c","start","",url], shell=False)
    else:
        import webbrowser; webbrowser.open(url)


def _ollama_chat(messages: list[dict[str,str]], temperature: float = 0.65) -> str:
    payload = {"model":OLLAMA_MODEL,"messages":messages,"stream":False,"options":{"temperature":temperature,"num_ctx":16000}}
    req = urllib.request.Request(f"{OLLAMA_URL.rstrip('/')}/api/chat",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=180) as response: data=json.loads(response.read().decode())
    except Exception as exc: raise RuntimeError(f"Could not reach Ollama at {OLLAMA_URL}: {exc}") from exc
    text = data.get("message",{}).get("content","").strip()
    if not text: raise RuntimeError("Ollama returned an empty message")
    return text


def _prospect_context(p) -> str:
    return (f"Name: {p['first_name']} {p['last_name']}\nLinkedIn: {p['url']}\nRelationship: {p['relationship']}\n"
            f"Company: {p['company'] or 'Unknown'}\nPosition/Headline: {p['position'] or 'Unknown'}\n"
            f"Location: {p['profile_location'] or 'Unknown'}\nProfile title: {p['profile_title'] or 'Unknown'}\n"
            f"Profile text:\n{p['profile_text'] or 'No additional profile text captured.'}")


def generate_initial(prospect_id: int) -> str:
    p=get_prospect(prospect_id)
    if not p: raise ValueError("Prospect not found")
    relation = "existing connection" if p["relationship"] == "connected" else "person you are not connected to; do not pretend you are already connected"
    prompt=("Write ONE highly personalized LinkedIn outreach message. Use only supplied facts; never invent facts. "
            "Reference a genuinely relevant detail from the profile, avoid generic praise and spammy sales wording, "
            "and keep it human and concise (50-100 words). This is an " + relation + ".\n\n" +
            f"PROSPECT\n{_prospect_context(p)}\n\nCONVERSATION\n{format_history(prospect_id)}")
    draft=_ollama_chat([{"role":"system","content":"You are an excellent one-to-one LinkedIn outreach writer."},{"role":"user","content":prompt}])
    save_draft(prospect_id,"initial",draft); return draft


def generate_reply(prospect_id: int) -> str:
    p=get_prospect(prospect_id)
    if not p: raise ValueError("Prospect not found")
    prompt=("Write the next LinkedIn reply in an ongoing one-to-one conversation. Reply directly to the latest inbound message, "
            "respect tone, use known context, never invent facts, and move the conversation forward naturally. 40-100 words.\n\n"
            f"PROSPECT\n{_prospect_context(p)}\n\nCONVERSATION\n{format_history(prospect_id)}")
    draft=_ollama_chat([{"role":"system","content":"You are a thoughtful LinkedIn conversation assistant."},{"role":"user","content":prompt}])
    save_draft(prospect_id,"reply",draft); return draft


def set_eliminated(prospect_id:int, eliminated:bool, reason:str|None=None)->None:
    with connect() as db:
        db.execute("UPDATE prospects SET eliminated=?,elimination_reason=?,eliminated_at=?,updated_at=? WHERE id=?",
                   (int(eliminated),reason.strip() if eliminated and reason else None,utc_now() if eliminated else None,utc_now(),prospect_id))


init_db()
