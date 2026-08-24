import sqlite3

db_path = r'C:\Users\Admin\Desktop\coding-agent\linkedin_intelligence\db\linkedin_intelligence.sqlite'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print("Verification - all tables:")
for table in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    status = "EMPTY" if count == 0 else f"HAS {count} ROWS"
    print(f"  {table}: {status}")
conn.close()
