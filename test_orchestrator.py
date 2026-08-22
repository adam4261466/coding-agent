import sys
import os
sys.path.insert(0, os.getcwd())

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH
from linkedin_intelligence.automation.memory.memory_service import MemoryService
from linkedin_intelligence.automation.actions.browser_executor import BrowserExecutor
from linkedin_intelligence.agent.orchestrator import AgentOrchestrator

print("Initializing store...")
store = Store(DB_PATH)
print("Store initialized.")

print("Initializing memory...")
memory = MemoryService(DB_PATH)
print("Memory initialized.")

print("Initializing browser...")
browser = BrowserExecutor()
print("Browser initialized.")

print("Creating orchestrator...")
orchestrator = AgentOrchestrator(store=store, memory=memory, browser=browser)
print("Orchestrator created.")

print("Starting one cycle...")
result = orchestrator.run_once()
print("Cycle result:", result)