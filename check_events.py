import sqlite3
c = sqlite3.connect('linkedin_intelligence/db/linkedin_intelligence.sqlite')
c.row_factory = sqlite3.Row
rows = c.execute('SELECT event_type, data_json FROM events WHERE event_type IN ("message_sent", "message_generated", "message_approved") ORDER BY created_at').fetchall()
for r in rows:
    print(r['event_type'], '|', r['data_json'][:120] if r['data_json'] else '')
c.close()