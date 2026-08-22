import sqlite3
c = sqlite3.connect('linkedin_intelligence/db/linkedin_intelligence.sqlite')
c.row_factory = sqlite3.Row
rows = c.execute('SELECT * FROM outreach_messages WHERE prospect_id = "p_11dd2f9c5d" AND campaign_id = "ai_engineers"').fetchall()
for r in rows:
    print(dict(r))
c.close()