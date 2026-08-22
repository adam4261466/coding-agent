import sqlite3
import os

db_path = 'linkedin_intelligence/db/linkedin_intelligence.sqlite'
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Clear handoffs and failed sends to break the agent loop
cursor.execute("DELETE FROM events WHERE event_type = 'handoff_requested'")
cursor.execute("DELETE FROM events WHERE data_json LIKE '%send_message%' AND data_json LIKE '%false%'")

conn.commit()
print(f"Successfully cleared stale events from {db_path}")
conn.close()
