import sys
import os
import traceback

# Add the current directory to the path
sys.path.insert(0, os.getcwd())

print("Starting debug test...")

try:
    print("Importing store...")
    from linkedin_intelligence.store import Store
    from linkedin_intelligence.utils import DB_PATH
    print("Store imported.")
    
    print("Initializing store...")
    store = Store(DB_PATH)
    print("Store initialized.")
    
    print("Getting prospects...")
    prospects = store.prospects()
    print(f"Found {len(prospects)} prospects.")
    
    if prospects:
        print("First prospect:", prospects[0])
    
    print("Importing memory service...")
    from linkedin_intelligence.automation.memory.memory_service import MemoryService
    print("Memory service imported.")
    
    print("Initializing memory...")
    memory = MemoryService(DB_PATH)
    print("Memory initialized.")
    
    print("Importing browser executor...")
    from linkedin_intelligence.automation.actions.browser_executor import BrowserExecutor
    print("Browser executor imported.")
    
    print("Initializing browser...")
    browser = BrowserExecutor()
    print("Browser initialized.")
    
    print("Importing orchestrator...")
    from linkedin_intelligence.agent.orchestrator import AgentOrchestrator
    print("Orchestrator imported.")
    
    print("Creating orchestrator...")
    orchestrator = AgentOrchestrator(
        store=store, 
        memory=memory, 
        browser=browser,
        dry_run=True,
        max_cycles=1
    )
    print("Orchestrator created.")
    
    print("Running one cycle...")
    result = orchestrator.run_once()
    print("Cycle completed.")
    print("Result:", result)
    
except Exception as e:
    print("ERROR:", e)
    traceback.print_exc()
finally:
    print("Debug test finished.")