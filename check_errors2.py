import sqlite3
import json

conn = sqlite3.connect('linkedin_intelligence/db/linkedin_intelligence.sqlite')
cursor = conn.cursor()
cursor.execute("""
SELECT prospect_id, data_json FROM events WHERE event_type='error' ORDER BY id DESC LIMIT 10
""")
rows = cursor.fetchall()
for pid, data_json in rows:
    print('ID:', pid)
    try:
        print('Data:', json.loads(data_json))
    except Exception as e:
        print('Error parsing JSON:', e)
        print('Raw data:', data_json)
    print()