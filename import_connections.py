"""Import the user's LinkedIn Connections.csv into the small local DB."""

from __future__ import annotations

import argparse
import csv
import os

from linkedin_agent import init_db, reset_db, upsert_prospect, connect

REQUIRED = {"First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"}


def import_csv(path: str, clean: bool = False) -> int:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
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
            raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            if not (row.get("First Name") or row.get("Last Name")):
                continue
            if not (row.get("URL") or "").strip():
                continue
            upsert_prospect(row)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Import LinkedIn Connections.csv")
    parser.add_argument("csv_path", nargs="?", default="Connections.csv")
    parser.add_argument("--clean", action="store_true", help="Delete and recreate linkedin_agent.db before import")
    args = parser.parse_args()
    n = import_csv(args.csv_path, clean=args.clean)
    with connect() as db:
        total = db.execute("SELECT COUNT(*) AS n FROM prospects").fetchone()["n"]
    print(f"Imported/updated: {n} rows")
    print(f"Prospects in DB:  {total}")
    print(f"Database:         {os.path.abspath('linkedin_agent.db')}")


if __name__ == "__main__":
    main()
