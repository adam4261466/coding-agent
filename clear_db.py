#!/usr/bin/env python3
"""Clear all data from the database while keeping the structure."""

import sqlite3
import os

db_path = r'C:\Users\Admin\Desktop\coding-agent\linkedin_intelligence\db\linkedin_intelligence.sqlite'

if not os.path.exists(db_path):
    print(f"Database not found: {db_path}")
    exit(1)

print(f"Clearing: {db_path}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]

# Delete all data from each table
for table in tables:
    cursor.execute(f"DELETE FROM {table}")
    print(f"  Cleared: {table}")

# Reset auto-increment counters
for table in tables:
    cursor.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))

conn.commit()

# Verify
print("\nVerification:")
for table in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"  {table}: {count} rows")

conn.close()
print("\nDatabase cleared successfully!")
