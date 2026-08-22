import sys
sys.path.insert(0, '.')

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH
from linkedin_intelligence.automation.memory.memory_service import MemoryService
from linkedin_intelligence.agent.planner import _build_planner_prompt, _fallback_plan
import json

store = Store(DB_PATH)
memory = MemoryService(DB_PATH)

# Get a prospect that is MESSAGE_PENDING
prospects = store.prospects()
for p in prospects:
    if p.get('status') == 'MESSAGE_PENDING':
        prospect_id = p.get('id')
        print(f"Found MESSAGE_PENDING prospect: {prospect_id}")
        print(f"Prospect data: {json.dumps(p, indent=2)[:500]}...")
        # Get context
        context = memory.get_prospect_context(prospect_id)
        print(f"\nContext keys: {list(context.keys())}")
        
        # Print important parts
        print(f"\nState: {json.dumps(context.get('state', {}), indent=2)}")
        print(f"\nFacts: {json.dumps(context.get('facts', {}), indent=2)}")
        print(f"\nProduct journey: {json.dumps(context.get('product_journey', {}), indent=2)}")
        print(f"\nConversation: {json.dumps(context.get('conversation_summary', {}), indent=2)}")
        print(f"\nRelationship: {json.dumps(context.get('relationship', {}), indent=2)}")
        print(f"\nActive task: {json.dumps(context.get('active_task'), indent=2)}")
        print(f"\nEligibility: {json.dumps(context.get('eligibility', []), indent=2)}")
        print(f"\nRecent events (first 3): {json.dumps(context.get('recent_events', [])[:3], indent=2)}")
        
        # Run fallback plan to see what it decides
        plan = _fallback_plan(context)
        print(f"\nFALLBACK PLAN: {plan}")
        
        # Build the prompt and see what it looks like
        prompt = _build_planner_prompt(context)
        print(f"\nPROMPT (first 1000 chars):\n{prompt[:1000]}")
        break

store.close()
memory.close()