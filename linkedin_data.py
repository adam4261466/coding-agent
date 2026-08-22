import csv
import io
import os
import re
from datetime import datetime

EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_export")

HEADER_ROWS = {
    "Connections.csv": "First Name,Last Name",
    "messages.csv": "thread_title",
    "guide_messages.csv": "thread_title",
    "learning_coach_messages.csv": "thread_title",
    "learning_role_play_messages.csv": "thread_title",
    "Company Follows.csv": "Organization,Followed On",
    "Education.csv": "School Name",
    "Email Addresses.csv": "Email Address",
    "Invitations.csv": "First Name",
    "Learning.csv": "Course Title",
    "PhoneNumbers.csv": "Phone Type",
    "Profile.csv": "First Name",
    "Profile Summary.csv": "Language",
    "Registration.csv": "First Name",
    "Rich_Media.csv": "Media Type",
    "SavedJobAlerts.csv": "Job Search Alert",
    "Jobs/Job Seeker Preferences.csv": "Preferences",
}


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _read_csv(fname: str) -> list:
    path = os.path.join(EXPORT_DIR, fname)
    if not os.path.exists(path):
        return []
    try:
        with io.open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return []
    header = HEADER_ROWS.get(fname)
    if header:
        start = next((k for k, line in enumerate(lines) if line.lstrip().startswith(header)), 0)
        lines = lines[start:]
    if not lines:
        return []
    try:
        return list(csv.DictReader(io.StringIO("\n".join(lines))))
    except Exception:
        return []


def _row_display(row: dict, keys: list) -> list:
    out = []
    for k in keys:
        v = _clean_text(row.get(k, ""))
        if v:
            out.append(f"{k}: {v}")
    return out


def connections(kw: str = "", limit: int = 30) -> str:
    rows = _read_csv("Connections.csv")
    if not rows:
        return "No Connections.csv found in the export."
    kw = kw.lower().strip()
    matched = []
    for r in rows:
        first = _clean_text(r.get("First Name", ""))
        last = _clean_text(r.get("Last Name", ""))
        full = f"{first} {last}".strip().lower()
        if kw and kw not in full:
            continue
        matched.append(r)
    total = len(matched)
    matched = matched[:limit]
    lines = [f"Connections matching '{kw or 'all'}': {total} total (showing up to {len(matched)})"]
    for r in matched:
        lines.append(
            "  - "
            + " | ".join(
                _row_display(
                    r,
                    ["First Name", "Last Name", "Company", "Position", "Connected On", "URL"],
                )
            )
        )
    return "\n".join(lines)


def connection(name: str) -> str:
    rows = _read_csv("Connections.csv")
    name = name.lower().strip()
    best = None
    for r in rows:
        full = f"{_clean_text(r.get('First Name',''))} {_clean_text(r.get('Last Name',''))}".lower()
        if full == name:
            best = r
            break
        if name in full and best is None:
            best = r
    if not best:
        return f"No connection found matching '{name}'."
    lines = _row_display(
        best,
        ["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"],
    )
    return "Connection:\n  " + "\n  ".join(lines)


def companies(kw: str = "", limit: int = 40) -> str:
    rows = _read_csv("Company Follows.csv")
    if not rows:
        return "No Company Follows.csv found."
    kw = kw.lower().strip()
    matched = [r for r in rows if not kw or kw in _clean_text(r.get("Organization", "")).lower()]
    lines = [f"Companies followed: {len(matched)} total (showing up to {limit})"]
    for r in matched[:limit]:
        lines.append("  - " + " | ".join(_row_display(r, ["Organization", "Followed On"])))
    return "\n".join(lines)


def messages(kw: str = "", limit: int = 40) -> str:
    rows = []
    for fname in ["messages.csv", "guide_messages.csv", "learning_coach_messages.csv", "learning_role_play_messages.csv"]:
        rows.extend(_read_csv(fname))
    if not rows:
        return "No message files found."
    kw = kw.lower().strip()
    matched = []
    for r in rows:
        hay = " ".join(_clean_text(str(r.get(k, ""))) for k in r).lower()
        if not kw or kw in hay:
            matched.append(r)
    lines = [f"Messages/threads: {len(matched)} total (showing up to {limit})"]
    for r in matched[:limit]:
        fields = _row_display(r, ["thread_title", "from", "to", "date", "sent_date", "text"])
        lines.append("  - " + " | ".join(fields) if fields else "  - (empty row)")
    return "\n".join(lines)


def invitations(kw: str = "", limit: int = 40) -> str:
    rows = _read_csv("Invitations.csv")
    if not rows:
        return "No Invitations.csv found."
    kw = kw.lower().strip()
    matched = [r for r in rows if not kw or kw in _clean_text(r.get("First Name", "")).lower()]
    lines = [f"Invitations: {len(matched)} total (showing up to {limit})"]
    for r in matched[:limit]:
        lines.append("  - " + " | ".join(_row_display(r, ["First Name", "Last Name", "URL", "Connected On"])))
    return "\n".join(lines)


def learning(limit: 20) -> str:
    rows = _read_csv("Learning.csv")
    if not rows:
        return "No Learning.csv found."
    lines = [f"LinkedIn Learning courses: {len(rows)}"]
    for r in rows[:limit]:
        lines.append("  - " + " | ".join(_row_display(r, ["Course Title", "Progress", "Completion Date", "Certificate URL"])))
    if len(rows) > limit:
        lines.append(f"  ... and {len(rows) - limit} more")
    return "\n".join(lines)


def profile() -> str:
    parts = []
    for fname in ["Profile.csv", "Email Addresses.csv", "Education.csv", "PhoneNumbers.csv", "Profile Summary.csv", "Registration.csv"]:
        rows = _read_csv(fname)
        if not rows:
            continue
        section = []
        for r in rows:
            section.append(" | ".join(_row_display(r, list(r.keys()))))
        parts.append(f"[{fname.replace('.csv','')}]\n  " + "\n  ".join(section))
    return "\n\n".join(parts) if parts else "No profile files found."


def digest() -> str:
    conns = _read_csv("Connections.csv")
    comps = _read_csv("Company Follows.csv")
    learn = _read_csv("Learning.csv")
    inv = _read_csv("Invitations.csv")
    emails = _read_csv("Email Addresses.csv")
    first = _clean_text(conns[0].get("First Name", "")) if conns else ""
    return (
        f"LinkedIn export digest:\n"
        f"  - Connections: {len(conns)}\n"
        f"  - Companies followed: {len(comps)}\n"
        f"  - Learning courses: {len(learn)}\n"
        f"  - Pending/outgoing invitations: {len(inv)}\n"
        f"  - Emails: {len(emails)}\n"
        f"  - First connection listed: {first}"
    )


ACTIONS = {
    "summary": digest,
    "connections": connections,
    "connection": connection,
    "companies": companies,
    "messages": messages,
    "invitations": invitations,
    "learning": learning,
    "profile": profile,
}


def linkedin_data(action: str = "summary", query: str = "", limit: int = 30) -> str:
    """Search the user's LinkedIn data export (offline, no browser needed)."""
    action = (action or "summary").strip().lower()
    if action not in ACTIONS:
        matches = [r for r in _read_csv("Connections.csv") if query.lower() in f"{r.get('First Name','')} {r.get('Last Name','')}".lower()]
        lines = [f"Unknown action '{action}'. Matching connections for '{query}': {len(matches)}"]
        for r in matches[:limit]:
            lines.append("  - " + " | ".join(_row_display(r, ["First Name", "Last Name", "Company", "Position", "URL"])))
        lines.append("Available actions: summary, connections, connection, companies, messages, invitations, learning, profile")
        return "\n".join(lines)
    try:
        return ACTIONS[action](query, limit)
    except TypeError:
        return ACTIONS[action]()
