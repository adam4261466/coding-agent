import sys
sys.path.insert(0, '.')
from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH

s = Store(DB_PATH)

# Status counts
ps = s.prospects()
statuses = {}
for p in ps:
    st = p.get("status", "?")
    statuses[st] = statuses.get(st, 0) + 1
print(f"Prospects: {len(ps)}")
for k, v in sorted(statuses.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

# Qualifications
quals = s.qualifications()
print(f"\nQualifications: {len(quals)}")

ready = [q for q in quals if q.get("recommended_next_action") in ("READY_FOR_HUMAN_REVIEW", "READY_FOR_OUTREACH")]
print(f"Ready for review/outreach: {len(ready)}")
for q in sorted(ready, key=lambda x: -(x.get("fit_score") or 0))[:30]:
    pid = q["prospect_id"]
    p = s.get_prospect(pid)
    name = p.get("full_name", pid) if p else pid
    print(f"  {name}: fit={q.get('fit_score')} pf={q.get('problem_fit_score')} conf={q.get('confidence')} action={q.get('recommended_next_action')}")

# Action breakdown
actions = {}
for q in quals:
    a = q.get("recommended_next_action", "?")
    actions[a] = actions.get(a, 0) + 1
print(f"\nAction breakdown:")
for k, v in sorted(actions.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

s.close()
