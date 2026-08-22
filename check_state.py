import sys
sys.path.insert(0, '.')

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH

store = Store(DB_PATH)

# Get all prospects
prospects = store.prospects()
print(f"Total prospects: {len(prospects)}")

# Count by status
status_counts = {}
for p in prospects:
    status = p.get('status', 'unknown')
    status_counts[status] = status_counts.get(status, 0) + 1

print("\nProspect status counts:")
for status, count in sorted(status_counts.items()):
    print(f"  {status}: {count}")

# Get research tasks
from linkedin_intelligence.store import Store
tasks = store.get_research_tasks()  # This might not exist, let's check the store methods
# Instead, we can use the memory service to get tasks
from linkedin_intelligence.automation.memory.memory_service import MemoryService
memory = MemoryService(DB_PATH)
active_tasks = memory.tasks.active_tasks()
print(f"\nActive tasks: {len(active_tasks)}")
for t in active_tasks[:5]:  # Show first 5
    print(f"  Prospect {t['prospect_id']}: goal={t['goal']}, completed={len(t.get('completed',[]))}, pending={len(t.get('pending',[]))}")

# Check for prospects that are MESSAGE_PENDING but have no research task completed
from linkedin_intelligence.automation.memory.memory_service import MemoryService
memory = MemoryService(DB_PATH)
prospect_ids_with_research_done = set()
for q in memory.store.qualifications():  # We don't have direct access, let's use the store's qualifications method if exists
    pass

# Let's just check a few prospects that are MESSAGE_PENDING and see if they have research
print("\nChecking MESSAGE_PENDING prospects for research:")
count = 0
for p in prospects:
    if p.get('status') == 'MESSAGE_PENDING':
        pid = p.get('id')  # The prospect id is stored in 'id' field as per schema
        # Check if there's a research task that is done
        # We can use the memory service to get the state for this prospect
        state = memory.get_state(pid)
        identity = state.get('identity', {})
        # Check if there are any research findings or if research task is done
        # For simplicity, let's just see if we have any qualifications from research
        # We'll break after a few
        count += 1
        if count >= 5:
            break
        print(f"  Prospect {pid}: name={identity.get('name')}, linkedin_url={p.get('linkedin_url')}")

store.close()
memory.close()