import sqlite3, json
conn = sqlite3.connect(r"C:\Users\Admin\Desktop\coding-agent\linkedin_intelligence\db\linkedin_intelligence.sqlite")
rows = conn.execute("SELECT id, event_type, data_json FROM events WHERE event_type = 'error' ORDER BY id DESC LIMIT 5").fetchall()
for r in rows:
    d = json.loads(r[2])
    print(f"{r[0]} {r[1]}: {json.dumps(d, indent=2, ensure_ascii=False)}")
conn.close()
