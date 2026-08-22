import sys
sys.path.insert(0, '.')

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH
from linkedin_intelligence.automation.monitoring.eligibility import sweep_all

store = Store(DB_PATH)
work_items = sweep_all(store)
print(f"Found {len(work_items)} work items")

for i, item in enumerate(work_items[:10]):  # Show first 10
    print(f"\nItem {i}:")
    print(f"  prospect_id: {item.get('prospect_id')}")
    print(f"  campaign_id: {item.get('campaign_id')}")
    print(f"  reason: {item.get('reason')}")
    print(f"  eligibility_checks: {item.get('eligibility_checks')}")
    prospect = store.get_prospect(item['prospect_id'])
    if prospect:
        print(f"  prospect status: {prospect.get('status')}")
        print(f"  prospect linkedin_url: {prospect.get('linkedin_url')}")
    else:
        print("  prospect: NOT FOUND")

store.close()