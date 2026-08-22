#!/usr/bin/env python3
"""Fully automatic LinkedIn outreach agent.

Runs the complete pipeline in a closed loop:
  discover -> research -> qualify -> segment -> produce -> execute -> monitor -> decide -> repeat

Usage:
  python run_agent.py                    # auto mode, run until interrupted
  python run_agent.py --dry-run          # plan only, no actions
  python run_agent.py --manual           # plan + execute requires human approval
  python run_agent.py --cycles 5         # run 5 cycles then stop
  python run_agent.py --delay 120        # 120s between idle cycles
  python run_agent.py --assign           # assign eligible prospects to campaigns
  python run_agent.py --assign --limit 10 # assign up to 10 prospects
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH


def main():
    parser = argparse.ArgumentParser(
        description="Fully automatic LinkedIn outreach agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan actions without executing them")
    parser.add_argument("--manual", action="store_true",
                        help="Require human approval before execution")
    parser.add_argument("--cycles", type=int, default=None,
                        help="Number of cycles to run (default: infinite)")
    parser.add_argument("--delay", type=int, default=60,
                        help="Seconds between idle cycles (default: 60)")
    parser.add_argument("--model", type=str, default="gemma4:31b-cloud",
                        help="Model for planning (default: gemma4:31b-cloud)")
    parser.add_argument("--browser-model", type=str, default="gemma4:31b-cloud",
                        help="Model for browser actions (default: gemma4:31b-cloud)")
    parser.add_argument("--base-url", type=str, default="http://localhost:11434",
                        help="Ollama base URL")
    parser.add_argument("--db", type=str, default=None,
                        help="Database path")
    parser.add_argument("--assign", action="store_true",
                        help="Assign eligible prospects to campaigns then run agent")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max prospects to assign per campaign (default: 10)")
    args = parser.parse_args()

    db_path = args.db or DB_PATH
    store = Store(db_path)

    if args.assign:
        _run_assign(store, args.limit)
        store.close()
        return

    from linkedin_intelligence.automation.memory.memory_service import MemoryService
    from linkedin_intelligence.automation.actions.browser_executor import BrowserExecutor
    from linkedin_intelligence.agent.orchestrator import AgentOrchestrator

    memory = MemoryService(db_path)
    browser = BrowserExecutor(model=args.browser_model, base_url=args.base_url)
    mode = "manual" if args.manual else "auto"

    orchestrator = AgentOrchestrator(
        store=store, memory=memory, browser=browser,
        model=args.model, base_url=args.base_url,
        mode=mode, dry_run=args.dry_run,
        max_cycles=args.cycles, cycle_delay=args.delay,
    )

    print(f"[run_agent] Database: {db_path}")
    print(f"[run_agent] Mode: {mode}")
    print(f"[run_agent] Dry run: {args.dry_run}")
    print(f"[run_agent] Browser model: {args.browser_model}")
    print()

    try:
        orchestrator.run()
    finally:
        store.close()
        memory.close()


def _run_assign(store, limit):
    """Assign eligible prospects to active campaigns."""
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
        print(f"  Started ( MESSAGE_PENDING): {result['started']}")


if __name__ == "__main__":
    main()
