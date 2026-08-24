#!/usr/bin/env python3
"""Check and clear the agent database."""

import sqlite3

db_path = r'C:\Users\Admin\Desktop\coding-agent\linkedin_intelligence\db\linkedin_intelligence_agent.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print('Tables:')
for table in tables:
    cursor.execute(f'SELECT COUNT(*) FROM {table}')
    count = cursor.fetchone()[0]
    print(f'  {table}: {count} rows')

print('\nClearing all tables...')
for table in tables:
    cursor.execute(f'DELETE FROM {table}')
    print(f'  Cleared: {table}')

conn.commit()

print('\nAfter clearing:')
for table in tables:
    cursor.execute(f'SELECT COUNT(*) FROM {table}')
    count = cursor.fetchone()[0]
    print(f'  {table}: {count} rows')

conn.close()
print('\nDone!')
