#!/usr/bin/env python3
"""Check database contents after running the LinkedIn agent."""

import sqlite3
import json
import os
import sys

# Force UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_intelligence", "db", "linkedin_intelligence.sqlite")

if not os.path.exists(db_path):
    print(f"Database not found: {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=" * 60)
print("NEXUS DATABASE CHECK - 2026-08-24")
print("=" * 60)

print("\n[1] DATABASE STRUCTURE")
print("-" * 40)
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
    print(f"  {t[0]}: {count} rows")

print("\n[2] PROSPECTS (Observed Profiles)")
print("-" * 40)
prospects = conn.execute(
    "SELECT id, full_name, current_company, raw_position, connection_status, status, linkedin_url "
    "FROM prospects ORDER BY rowid DESC"
).fetchall()
if not prospects:
    print("  WARNING: No prospects found!")
else:
    for p in prospects:
        print(f"  Name:        {p['full_name'] or 'N/A'}")
        print(f"  ID:          {p['id']}")
        print(f"  Company:     {p['current_company'] or 'N/A'}")
        print(f"  Position:    {p['raw_position'] or 'N/A'}")
        print(f"  Connection:  {p['connection_status'] or 'N/A'}")
        print(f"  Status:      {p['status'] or 'N/A'}")
        print(f"  LinkedIn:    {p['linkedin_url'] or 'N/A'}")
        print()

print("\n[3] EVIDENCE (Observations Made)")
print("-" * 40)
evidence = conn.execute(
    "SELECT evidence_id, prospect_id, claim, observation, source_type, confidence, created_at "
    "FROM evidence ORDER BY created_at DESC LIMIT 30"
).fetchall()
if not evidence:
    print("  WARNING: No evidence found! Agent may not have observed profiles correctly.")
else:
    for e in evidence:
        print(f"  Prospect:    {e['prospect_id']}")
        print(f"  Claim:       {e['claim'][:120] if e['claim'] else 'N/A'}...")
        if e['observation']:
            print(f"  Observation: {e['observation'][:120] if e['observation'] else 'N/A'}...")
        print(f"  Type:        {e['source_type']}")
        print(f"  Confidence:  {e['confidence']}")
        print(f"  Time:        {e['created_at']}")
        print()

print("\n[4] AGENT PERMISSIONS")
print("-" * 40)
permissions = conn.execute("SELECT * FROM agent_permissions").fetchall()
if not permissions:
    print("  WARNING: No permissions set!")
else:
    for p in permissions:
        print(f"  Prospect:    {p['prospect_id']}")
        print(f"  Permission:  {p['permission']}")
        print(f"  View Profile: {p['view_profile']}")
        print(f"  Send Connect: {p['send_connection']}")
        print(f"  Send Message: {p['send_message']}")
        print(f"  Reply:        {p['reply']}")
        print(f"  Follow Up:    {p['follow_up']}")
        print(f"  Notes:        {p['notes'] or 'N/A'}")
        print()

print("\n[5] QUALIFICATIONS")
print("-" * 40)
quals = conn.execute(
    "SELECT prospect_id, fit_score, problem_fit_score, confidence, method, reason, created_at "
    "FROM qualifications ORDER BY created_at DESC LIMIT 10"
).fetchall()
if not quals:
    print("  WARNING: No qualifications found!")
else:
    for q in quals:
        print(f"  Prospect:    {q['prospect_id']}")
        print(f"  Fit Score:   {q['fit_score']}")
        print(f"  Confidence:  {q['confidence']}")
        print(f"  Method:      {q['method']}")
        print(f"  Reason:      {q['reason'][:100] if q['reason'] else 'N/A'}...")
        print(f"  Time:        {q['created_at']}")
        print()

print("\n[6] SUMMARY")
print("-" * 40)
stats = {}
for t in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
    stats[t[0]] = count

print(f"  Total prospects loaded:    {stats.get('prospects', 0)}")
print(f"  Total evidence recorded:   {stats.get('evidence', 0)}")
print(f"  Total qualifications:      {stats.get('qualifications', 0)}")
print(f"  Total permissions set:     {stats.get('agent_permissions', 0)}")
print(f"  Total research tasks:      {stats.get('research_tasks', 0)}")

if stats.get('prospects', 0) > 0 and stats.get('evidence', 0) > 0:
    print("\n  ✓ Agent appears to have stored data correctly!")
elif stats.get('prospects', 0) > 0:
    print("\n  ⚠ Prospects exist but no evidence recorded yet.")
else:
    print("\n  ✗ No prospect data found. Agent may not have run correctly.")

conn.close()
print("\n" + "=" * 60)
