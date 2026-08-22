import sys
sys.path.insert(0, '.')

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH
from linkedin_intelligence.automation.memory.memory_service import MemoryService
from linkedin_intelligence.agent.planner import plan_next_action

store = Store(DB_PATH)
memory = MemoryService(DB_PATH)

# Get a prospect that is MESSAGE_PENDING
prospects = store.prospects()
for p in prospects:
    if p.get('status') == 'MESSAGE_PENDING':
        prospect_id = p.get('id')
        print(f"Found MESSAGE_PENDING prospect: {prospect_id}")
        # Get context
        context = memory.get_prospect_context(prospect_id)
        print(f"Context keys: {list(context.keys())}")
        # Get campaign
        campaign_id = context.get('campaign_id')
        campaign = store.get_campaign(campaign_id) if campaign_id else None
        print(f"Campaign: {campaign}")
        # Call planner
        plan = plan_next_action(memory, p, campaign or {}, outreach_state='MESSAGE_PENDING')
        print(f"Plan: {plan}")
        break

store.close()
memory.close()