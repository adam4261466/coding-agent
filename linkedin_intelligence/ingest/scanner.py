"""Auto-discover the export schema. The pipeline never assumes the CSV layout."""

import csv
import io
import os
from datetime import datetime

# First cell hint used to locate the real header row after any "Notes:"
# preamble LinkedIn prepends to some files.
HEADER_HINTS = {
    "Connections.csv": "First Name",
    "messages.csv": "CONVERSATION ID",
    "guide_messages.csv": "CONVERSATION ID",
    "learning_coach_messages.csv": "CONVERSATION ID",
    "learning_role_play_messages.csv": "CONVERSATION ID",
    "Company Follows.csv": "Organization",
    "Education.csv": "School Name",
    "Email Addresses.csv": "Email Address",
    "Invitations.csv": "From",
    "Learning.csv": "Course Title",
    "PhoneNumbers.csv": "Phone Type",
    "Profile.csv": "First Name",
    "Profile Summary.csv": "Language",
    "Registration.csv": "First Name",
    "Rich_Media.csv": "Media Type",
    "SavedJobAlerts.csv": "Job Search Alert",
    "Jobs": "Preferences",
}

DATE_HINT_WORDS = ("date", "on", "sent", "at", "followed", "connected",
                   "completed", "updated", "start", "end", "time")
ID_HINT_WORDS = ("url", "id", "email", "profile", "conversation")


def _try_date(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return False
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%Y-%m-%d %H:%M:%S", "%m/%d/%y",
                "%m/%d/%Y", "%b %d %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            datetime.strptime(v, fmt)
            return True
        except ValueError:
            continue
    return False


def _parse_rows(text: str):
    return list(csv.reader(io.StringIO(text), delimiter=","))


def _detect_header_row(rows, hint):
    if hint:
        for i, row in enumerate(rows):
            if row and row[0].strip().lstrip('\ufeff').startswith(hint):
                return i
    for i, row in enumerate(rows):
        if not row:
            continue
        cells = [c for c in row if c.strip()]
        if len(cells) >= 2 and all(c.isalpha() or c.isspace() for c in " ".join(cells).replace(" ", "")):
            return i
    return 0


def inspect_file(path: str, hint: str = None) -> dict:
    fname = os.path.basename(path)
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return {"filename": fname, "usable": False, "error": str(e)}

    lines = text.splitlines()
    # Skip the optional single-line Notes preamble so it isn't mistaken for a header.
    body = "\n".join(lines)
    rows = _parse_rows(body)
    header_idx = _detect_header_row(rows, hint)

    header = rows[header_idx] if header_idx < len(rows) else []
    data = rows[header_idx + 1:]
    data = [r for r in data if any(c.strip() for c in r)]

    header = [h.strip().lstrip('\ufeff') for h in header]
    col_count = len(header)
    empty_cols = []
    date_cols = []
    id_cols = []
    for c, name in enumerate(header):
        values = [row[c] for row in data if c < len(row)]
        if not any(v.strip() for v in values):
            empty_cols.append(name)
        if any(_try_date(v) for v in values[:20]) or any(w in name.lower() for w in DATE_HINT_WORDS):
            date_cols.append(name)
        if any(w in name.lower() for w in ID_HINT_WORDS):
            id_cols.append(name)

    seen = set()
    dup_rows = 0
    for r in data:
        key = tuple(cell.strip().lower() for cell in r)
        if key in seen:
            dup_rows += 1
        else:
            seen.add(key)

    return {
        "filename": fname,
        "columns": header,
        "column_count": col_count,
        "row_count": len(data),
        "empty_columns": empty_cols,
        "duplicate_rows": dup_rows,
        "date_columns": date_cols,
        "identifier_columns": id_cols,
        "header_line": header_idx + 1,
        "usable": bool(header and data),
    }


def scan(source_dir: str) -> dict:
    report = {"source_dir": source_dir, "files": {}, "total_files": 0}
    if not os.path.isdir(source_dir):
        report["error"] = f"source directory not found: {source_dir}"
        return report

    for entry in sorted(os.scandir(source_dir), key=lambda e: e.name):
        if entry.name.lower().startswith(".") or entry.is_dir():
            continue
        if not entry.name.lower().endswith(".csv"):
            continue
        hint = HEADER_HINTS.get(entry.name)
        report["files"][entry.name] = inspect_file(entry.path, hint)
        report["total_files"] += 1

    return report


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "linkedin_export")
    result = scan(src)
    print(f"scanned {result['total_files']} files -> data/reports/import_report.json")
    for fname, info in result["files"].items():
        print(f"  {fname}: rows={info.get('row_count')} cols={info.get('column_count')} "
              f"usable={info.get('usable')}")
