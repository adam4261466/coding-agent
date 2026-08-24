import sqlite3

DB_PATH = r"C:\Users\Admin\Desktop\coding-agent\linkedin_intelligence\db\linkedin_intelligence.sqlite"

conn = sqlite3.connect(DB_PATH)

print("DATABASE:")
print(DB_PATH)
print()
print("TABLES:")
print("-" * 40)

rows = conn.execute("""
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
    ORDER BY name
""").fetchall()

for row in rows:
    print(row[0])

conn.close()