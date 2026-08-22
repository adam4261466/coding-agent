import json
from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH
import os

store = Store(DB_PATH)

# 1. Clear Phase 2 tables
for table in ("qualifications", "research_tasks", "evidence", "segments"):
    count = store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    store.conn.execute(f"DELETE FROM {table}")
    print(f"  Cleared {table}: {count} rows")

# 2. Reset all prospect statuses and clear qualification data from data_json
rows = store.conn.execute("SELECT id, data_json FROM prospects").fetchall()
reset = 0
for row in rows:
    pid = row["id"]
    d = json.loads(row["data_json"])
    changed = False
    for key in ("qualification_fit", "qualification_confidence", "research_mode",
                "pain_state", "next_action", "segments", "segmentation_status",
                "missing_signals", "selection_reasons", "problem_fit_score"):
        if d.get(key) is not None:
            d[key] = None if key != "segments" else []
            changed = True
    if d.get("status") != "new":
        d["status"] = "new"
        changed = True
    if changed:
        store.conn.execute(
            "UPDATE prospects SET status = 'new', data_json = ? WHERE id = ?",
            (json.dumps(d, ensure_ascii=False), pid))
        reset += 1
store.conn.commit()
print(f"  Reset {reset} prospects to status=new")

# 3. Delete output files
for f in [
    "linkedin_intelligence/data/intelligence/segments_research.json",
    "linkedin_intelligence/data/reports/final_report.json",
    "linkedin_intelligence/data/reports/final_report.md",
    "linkedin_intelligence/data/reports/calibration_report.json",
    "linkedin_intelligence/data/reports/calibration_report.md",
]:
    full = os.path.join(os.path.dirname(__file__), f)
    if os.path.exists(full):
        os.remove(full)
        print(f"  Deleted {f}")

store.close()
print("\nPhase 2 completely wiped. Ready for fresh run.")