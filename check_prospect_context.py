import sys
sys.path.insert(0, '.')

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH
from linkedin_intelligence.automation.memory.memory_service import MemoryService

store = Store(DB_PATH)
memory = MemoryService(DB_PATH)

# Get a prospect that is MESSAGE_PENDING
prospects = store.prospects()
for p in prospects:
    if p.get('status') == 'MESSAGE_PENDING':
        prospect_id = p.get('id')
        print(f"Checking prospect {prospect_id}")
        # Get context from memory
        context = memory.get_prospect_context(prospect_id)
        print(f"  State: {context.get('state')}")
        print(f"  Facts: {context.get('facts')}")
        print(f"  Recent events: {len(context.get('recent_events', []))}")
        for i, ev in enumerate(context.get('recent_events', [])[-5:]):
            print(f"    {i}: {ev.get('event_type')} - {ev.get('action', 'no action')}")
        # Check if there's any research task completed
        # We can look for qualifications with task_id and then check the task status
        qualifications = memory.store.qualifications()  # This is a method on the store via memory?
        # Actually, memory.store is the Store instance? Let's check the MemoryService.
        # Looking at the code, MemoryService has a store attribute that is the Store.
        quals = memory.store.qualifications()
        research_done = False
        for q in quals:
            if q.get('prospect_id') == prospect_id:
                task_id = q.get('task_id')
                if task_id:
                    task = memory.store.get_research_task(task_id)
                    if task and task.get('status') == 'done':
                        research_done = True
                        break
        print(f"  Research done (via qualification): {research_done}")
        # Also check for profile_observed or research_prospect in recent events
        research_done_in_events = any(
            e.get('event_type') == 'profile_observed' or
            (e.get('data', {}).get('action') == 'research_prospect')
            for e in context.get('recent_events', [])
        )
        print(f"  Research done (in recent events): {research_done_in_events}")
        break

store.close()
memory.close()