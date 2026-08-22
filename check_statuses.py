import sqlite3
c = sqlite3.connect('linkedin_intelligence/db/linkedin_intelligence.sqlite')
c.row_factory = sqlite3.Row
rows = c.execute('SELECT status, COUNT(*) as cnt FROM prospects GROUP BY status').fetchall()
for r in rows:
    print(f"  {r['status']}: {r['cnt']}")
c.close()