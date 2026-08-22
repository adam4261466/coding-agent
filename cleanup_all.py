import sqlite3
import os

for db in ['linkedin_intelligence/db/linkedin_intelligence.sqlite', 
           'linkedin_intelligence/db/linkedin_intelligence_agent.db']:
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        # Clear handoffs, failed sends, and error events to break the agent loop
        conn.execute("DELETE FROM events WHERE event_type = 'handoff_requested'")
        conn.execute("DELETE FROM events WHERE event_type = 'message_sent' AND data_json LIKE '%false%'")
        conn.execute("DELETE FROM events WHERE event_type = 'error' AND data_json LIKE '%send_message%'")
        # Also clear old agent decisions about send/handoff
        conn.execute("DELETE FROM events WHERE event_type = 'agent_decision' AND (data_json LIKE '%send_message%' OR data_json LIKE '%handoff%')")
        conn.commit()
        conn.close()
        print(f"Cleared: {db}")
