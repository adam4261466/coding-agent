import sqlite3
from linkedin_intelligence.outreach.eligibility import is_eligible
from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import campaign_config

store = Store('linkedin_intelligence/db/linkedin_intelligence.sqlite')

# Get campaign
campaigns = store.campaigns(status='active')
if not campaigns:
    print("No active campaigns")
    exit(1)

campaign = campaigns[0]
cfg = campaign_config(campaign)
print(f"Campaign: {campaign['campaign_id']}")
print(f"Target segments: {cfg.get('target', {}).get('segments', [])}")
print(f"Min evidence confidence: {cfg.get('limits', {}).get('min_evidence_confidence', 0.5)}")

# Get first 5 prospects
prospects = store.prospects(limit=5)
for p in prospects:
    pid = p['prospect_id']
    ok, checks = is_eligible(store, p, campaign)
    print(f"\n{pid} ({p.get('full_name', 'unknown')}): eligible={ok}")
    for passed, reason in checks:
        print(f"  {'OK' if passed else 'FAIL'}: {reason}")

store.close()