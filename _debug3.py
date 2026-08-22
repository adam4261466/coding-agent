import sys, json
sys.stdout.reconfigure(encoding="utf-8")
from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH

store = Store(DB_PATH)

# Check messages
msgs = store.messages_for(prospect_id="p_549a644db9")
print(f"=== MESSAGES ({len(msgs)}) ===")
for m in msgs:
    text = m.get("text") or ""
    print(f"  {m.get('message_id')}  status={m.get('status')}  v{m.get('version')}")
    print(f"    text: {text[:150]}")
    print(f"    validation: {m.get('validation')}")
    print(f"    claims: {m.get('claims')}")
    print(f"    approved: {m.get('approved')}")
    print()

# Check recent events
from linkedin_intelligence.automation.memory.event_store import EventStore
import os
agent_db = DB_PATH.replace(".sqlite", "_agent.db")
if os.path.exists(agent_db):
    es = EventStore(agent_db)
    events = es.conn.execute(
        "SELECT id, event_type, data_json FROM events ORDER BY id DESC LIMIT 15"
    ).fetchall()
    print(f"=== RECENT AGENT EVENTS ({len(events)}) ===")
    for e in events:
        data = json.loads(e[2]) if e[2] else {}
        print(f"  {e[0]} {e[1]}: {json.dumps(data, ensure_ascii=False)[:120]}")
    es.close()

# Check main events
events = store.conn.execute(
    "SELECT id, event_type, data_json FROM events ORDER BY id DESC LIMIT 15"
).fetchall()
print(f"\n=== RECENT MAIN EVENTS ({len(events)}) ===")
for e in events:
    data = json.loads(e[2]) if e[2] else {}
    print(f"  {e[0]} {e[1]}: {json.dumps(data, ensure_ascii=False)[:120]}")

store.close()
