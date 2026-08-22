import sys
import os
print("Current directory:", os.getcwd())
print("sys.path[:3]:", sys.path[:3])

# Try to import the agent
try:
    from agent import Agent
    print("Successfully imported Agent")
    # Try to instantiate
    agent = Agent()
    print("Successfully instantiated Agent")
except Exception as e:
    print("Failed to import or instantiate Agent:", e)
    import traceback
    traceback.print_exc()