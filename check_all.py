import sqlite3
c = sqlite3.connect('linkedin_intelligence/db/linkedin_intelligence.sqlite')
c.row_factory = sqlite3.Row

# Count prospects
prospects = c.execute('SELECT COUNT(*) as cnt FROM prospects').fetchone()
print(f"Total prospects: {prospects['cnt']}")

# Count campaign prospects
cps = c.execute('SELECT COUNT(*) as cnt FROM campaign_prospects').fetchone()
print(f"Assigned to campaigns: {cps['cnt']}")

# Show campaigns
campaigns = c.execute('SELECT campaign_id, name, status FROM campaigns').fetchall()
for camp in campaigns:
    print(f"  Campaign: {camp['campaign_id']} ({camp['name']}) - {camp['status']}")
    cnt = c.execute('SELECT COUNT(*) as cnt FROM campaign_prospects WHERE campaign_id = ?', (camp['campaign_id'],)).fetchone()
    print(f"    Assigned: {cnt['cnt']}")

c.close()