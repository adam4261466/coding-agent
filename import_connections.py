"""Import the local connections.csv file into the small LinkedIn DB."""

from __future__ import annotations

import csv
import io
import os

from linkedin_agent import init_db, reset_db, upsert_prospect, connect

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "connections.csv")

REQUIRED = {
    "First Name",
    "Last Name",
    "URL",
    "Email Address",
    "Company",
    "Position",
    "Connected On",
}


def _read_linkedin_rows(path: str) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        lines = fh.read().splitlines()

    header_index = None
    for index, line in enumerate(lines):
        columns = next(csv.reader([line]), [])
        if set(columns) >= REQUIRED:
            header_index = index
            break

    if header_index is None:
        raise ValueError(
            "Could not find the LinkedIn CSV header. Expected columns: "
            + ", ".join(sorted(REQUIRED))
        )

    csv_text = "\n".join(lines[header_index:])
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = set(reader.fieldnames or [])
    missing = REQUIRED - headers
    if missing:
        raise ValueError(
            "connections.csv is missing columns: "
            + ", ".join(sorted(missing))
        )
    return list(reader)


def import_csv(path: str = CSV_PATH, clean: bool = False) -> int:
    if not os.path.exists(path):
        raise FileNotFoundError(f"connections.csv not found: {path}")

    if clean:
        reset_db()
    else:
        init_db()

    count = 0
    for row in _read_linkedin_rows(path):
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        url = (row.get("URL") or "").strip()
        if not (first or last) or not url:
            continue
        upsert_prospect(row)
        count += 1

    return count


def main() -> None:
    n = import_csv(clean=False)
    with connect() as db:
        total = db.execute("SELECT COUNT(*) AS n FROM prospects").fetchone()["n"]

    print(f"Imported/updated: {n} rows")
    print(f"Prospects in DB:  {total}")
    print(f"CSV:              {CSV_PATH}")
    print(f"Database:         {os.path.abspath(os.path.join(BASE_DIR, 'linkedin_agent.db'))}")


if __name__ == "__main__":
    main()
