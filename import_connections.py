"""Import the local connections.csv file into the small LinkedIn DB."""

from __future__ import annotations

import csv
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


def import_csv(path: str = CSV_PATH, clean: bool = True) -> int:
    if not os.path.exists(path):
        raise FileNotFoundError(f"connections.csv not found: {path}")

    if clean:
        reset_db()
    else:
        init_db()

    count = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = set(reader.fieldnames or [])
        missing = REQUIRED - headers
        if missing:
            raise ValueError(
                "connections.csv is missing columns: "
                + ", ".join(sorted(missing))
            )

        for row in reader:
            if not (row.get("First Name") or row.get("Last Name")):
                continue
            if not (row.get("URL") or "").strip():
                continue
            upsert_prospect(row)
            count += 1

    return count


def main() -> None:
    n = import_csv()
    with connect() as db:
        total = db.execute("SELECT COUNT(*) AS n FROM prospects").fetchone()["n"]

    print(f"Imported:         {n} rows")
    print(f"Prospects in DB:  {total}")
    print(f"CSV:              {CSV_PATH}")
    print(f"Database:         {os.path.abspath(os.path.join(BASE_DIR, 'linkedin_agent.db'))}")


if __name__ == "__main__":
    main()
