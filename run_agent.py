#!/usr/bin/env python3
"""Permission-gated LinkedIn outreach operator.

Deterministic loop: QUEUE -> PERMISSION GATE -> OPERATOR (LLM only for
language) -> MEMORY. The human decides who may be contacted and what may
be done via per-prospect permission records; the agent only obeys.

Usage:
  python run_agent.py                          # run until interrupted
  python run_agent.py --dry-run                # plan only, no actions
  python run_agent.py --cycles 5               # 5 ticks then stop
  python run_agent.py --delay 120              # 120s idle sleep between ticks
  python run_agent.py --permissions perms.json # load permissions, then exit
  python run_agent.py --assign                 # assign prospects to campaigns
"""

import argparse
import json
import os
import sys
import threading
import traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH

_DEBUG_ON = os.environ.get("AGENT_DEBUG", "1") != "0"
_LOG_PATH = os.path.join(ROOT, "agent_debug.log")


def _dbg(msg: str):
    if not _DEBUG_ON:
        return
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        thread = threading.current_thread().name
        line = f"[{ts}] [run_agent] [{thread}] {msg}"
        print(line[:4000], file=sys.stderr, flush=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Permission-gated LinkedIn outreach operator")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show gated actions without executing them")
    parser.add_argument("--cycles", type=int, default=None,
                        help="Number of ticks to run (default: infinite)")
    parser.add_argument("--delay", type=int, default=60,
                        help="Seconds to sleep when idle (default: 60)")
    parser.add_argument("--browser-model", type=str,
                        default="gemma4:31b-cloud",
                        help="Model for browser actions")
    parser.add_argument("--base-url", type=str,
                        default="http://localhost:11434",
                        help="Ollama base URL")
    parser.add_argument("--db", type=str, default=None,
                        help="Database path")
    parser.add_argument("--permissions", type=str, default=None,
                        help="Load per-prospect permissions JSON then exit")
    parser.add_argument("--assign", action="store_true",
                        help="Assign eligible prospects to campaigns")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max prospects to assign per campaign")
    args = parser.parse_args()
    _dbg(f"main() args: dry_run={args.dry_run} cycles={args.cycles} "
         f"permissions={args.permissions} assign={args.assign}")

    db_path = args.db or DB_PATH
    store = Store(db_path)

    try:
        if args.permissions:
            _load_permissions(store, args.permissions)
            return

        if args.assign:
            _run_assign(store, args.limit)
            return

        from linkedin_intelligence.automation.memory.memory_service import \
            MemoryService
        from linkedin_intelligence.automation.actions.browser_executor import \
            BrowserExecutor
        from linkedin_intelligence.agent.operator import AgentOperator

        memory = MemoryService(db_path)
        browser = BrowserExecutor(model=args.browser_model,
                                  base_url=args.base_url)
        operator = AgentOperator(
            store=store, memory=memory, browser=browser,
            base_url=args.base_url, dry_run=args.dry_run,
            max_cycles=args.cycles, cycle_delay=args.delay)

        summary = operator.queue.summary()
        print(f"[run_agent] Database: {db_path}")
        print(f"[run_agent] Queue: {summary['allowed']} allowed | "
              f"{summary['manual']} manual review | "
              f"{summary['blocked']} blocked")
        print(f"[run_agent] Dry run: {args.dry_run}")
        print()

        try:
            operator.run()
            _dbg("operator.run() finished normally")
        except Exception as e:
            _dbg(f"EXCEPTION operator.run(): {e}\n{traceback.format_exc()}")
            raise
        finally:
            memory.close()
    finally:
        store.close()


def _load_permissions(store, path: str):
    from linkedin_intelligence.agent.permissions import (
        load_json, PERMISSION_FLAGS)
    records = load_json(path)
    applied = 0
    for pid, spec in records.items():
        prospect = store.get_prospect(pid)
        if not prospect:
            matches = [p for p in store.prospects()
                       if p.get("full_name") == pid]
            if len(matches) == 1:
                pid = matches[0]["prospect_id"]
                prospect = matches[0]
        if not prospect:
            print(f"[permissions] WARNING: no prospect for {pid!r}, skipped")
            continue
        flag_subset = {k: v for k, v in spec.items()
                       if k in PERMISSION_FLAGS}
        record = store.set_permission(pid, permission=spec["permission"],
                                      flags=flag_subset,
                                      notes=spec.get("notes"))
        applied += 1
        flags_on = ", ".join(
            k for k in ("view_profile", "send_connection", "send_message",
                        "reply", "follow_up") if record[k])
        print(f"[permissions] {prospect.get('full_name', pid)}: "
              f"{record['permission']} ({flags_on})")
    print(f"[permissions] {applied}/{len(records)} applied")


def _run_assign(store, limit):
    """Assign eligible prospects to active campaigns."""
    _dbg(f"_run_assign limit={limit}")
    from linkedin_intelligence.outreach.campaign import (
        sync_campaigns, assign_batch)

    campaigns = sync_campaigns(store)
    active = [c for c in campaigns if c.get("status") in ("active", "synced")]
    if not active:
        active = [c for c in campaigns]
        if not active:
            print("[assign] No campaigns found. Create one in config/campaigns/")
            return

    all_prospects = store.prospects()
    print(f"[assign] {len(all_prospects)} prospects in database")
    print(f"[assign] {len(active)} active campaign(s)")

    for campaign in active:
        cid = campaign["campaign_id"]
        name = campaign.get("name", cid)
        existing = store.campaign_prospects(cid)
        existing_ids = {cp["prospect_id"] for cp in existing}
        available = [p for p in all_prospects
                     if p["prospect_id"] not in existing_ids]
        print(f"\n[assign] Campaign: {name} ({cid})")
        print(f"  Already assigned: {len(existing)}")
        print(f"  Available: {len(available)}")

        if not available:
            print("  No new prospects to assign")
            continue

        result = assign_batch(store, campaign, available, limit=limit)
        print(f"  Eligible: {result['eligible']}")
        print(f"  Assigned: {result['assigned']}")
        print(f"  Started (MESSAGE_PENDING): {result['started']}")


if __name__ == "__main__":
    main()
