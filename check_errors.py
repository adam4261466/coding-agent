import sqlite3
import json

conn = sqlite3.connect('linkedin_intelligence/db/linkedin_intelligence.sqlite')
cursor = conn.cursor()
cursor.execute("""
SELECT prospect_id, data FROM events WHERE event_type='error' ORDER BY id DESC LIMIT 10
""")
rows = cursor.fetchall()
for pid, data in rows:
    print('ID:', pid)
    try:
        print('Data:', json.loads(data))
    except:
        print('Data (raw):', data)
    print()