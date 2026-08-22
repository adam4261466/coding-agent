"""Backfill school field on prospects from Education.csv."""
import csv
import json
import os
from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH

edu_path = os.path.join(os.path.dirname(__file__),
                        "linkedin_export", "Education.csv")
if not os.path.exists(edu_path):
    print(f"Education.csv not found at {edu_path}")
    exit(1)

# Build name -> schools mapping
schools_by_name = {}
with open(edu_path, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        name = f"{row.get('First Name', '')} {row.get('Last Name', '')}".strip().lower()
        school = row.get("School Name", "").strip()
        if name and school:
            if name not in schools_by_name:
                schools_by_name[name] = []
            schools_by_name[name].append(school)

print(f"Loaded education for {len(schools_by_name)} names from Education.csv")

store = Store(DB_PATH)
rows = store.conn.execute("SELECT id, data_json FROM prospects").fetchall()
updated = 0
for row in rows:
    pid = row["id"]
    d = json.loads(row["data_json"])
    full_name = d.get("full_name", "").strip().lower()
    if full_name in schools_by_name:
        d["schools"] = schools_by_name[full_name]
    else:
        d["schools"] = []
    store.conn.execute(
        "UPDATE prospects SET data_json = ? WHERE id = ?",
        (json.dumps(d, ensure_ascii=False), pid))
    updated += 1
store.conn.commit()
print(f"Updated {updated} prospects with school data")

# Show some examples
sample = [json.loads(r["data_json"]) for r in rows[:5]]
for s in sample:
    print(f"  {s['full_name']}: {s.get('schools', [])}")

store.close()
