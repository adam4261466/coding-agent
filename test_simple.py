print("Starting test")
import sys
print("Python version:", sys.version)
sys.path.insert(0, '.')
print("Current sys.path:", sys.path[:3])
try:
    from linkedin_intelligence.store import Store
    print("Imported Store")
except Exception as e:
    print("Failed to import Store:", e)
    import traceback
    traceback.print_exc()
print("Test done")