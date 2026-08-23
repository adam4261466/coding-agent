"""Agent orchestrator: runs the full pipeline in a closed loop.

The orchestrator is the main loop that ties everything together:
  1. Load task state (crash recovery)
  2. Build context from memory
  3. Ask the planner what to do
  4. Execute the decision
  5. Record new events
  6. Update state
  7. Take snapshot
  8. Repeat

The agent runs in two modes:
  - auto (default): executes actions without human approval
  - manual: plans but requires human approval before execution
"""

import time
from datetime import datetime, timezone

from ..automation.memory.memory_service import MemoryService
from ..automation.monitoring.eligibility import sweep_all
from ..automation.actions.browser_executor import BrowserExecutor
from .planner import plan_next_action
from .executor import execute_plan


DEFAULT_MODEL = "qwen3.5:0.8b"
DEFAULT_BASE_URL = "http://localhost:11434"


class AgentOrchestrator:
    def __init__(self, store, memory: MemoryService = None,
                 browser: BrowserExecutor = None,
                 model: str = DEFAULT_MODEL,
                 base_url: str = DEFAULT_BASE_URL,
                 mode: str = "auto",
                 dry_run: bool = False,
                 max_cycles: int = None,
                 cycle_delay: int = 60):
        self.store = store
        self.memory = memory or MemoryService(store.path.replace(".db", "_agent.db"))
        self.browser = browser or BrowserExecutor(model=model, base_url=base_url)
        self.model = model
        self.base_url = base_url
        self.mode = mode
        self.dry_run = dry_run
        self.max_cycles = max_cycles
        self.cycle_delay = cycle_delay
        self._cycle_count = 0
        # In-memory circuit breaker: (prospect_id, action) -> consecutive
        # non-productive attempts in a row. This is what actually stops
        # the fast loop - previously a rate-limited or handed-off
        # prospect got re-planned and re-attempted on EVERY cycle
        # (~2s apart), spamming duplicate handoff events and burning
        # planner calls forever with nothing ever changing.
        self._streak = {}
        self.STREAK_LIMIT = 3
        # Thread-safe pause flag - set by the dashboard GUI to freeze the
        # loop between cycles without killing the process.
        self.paused = False

    def run(self):
        """Main agent loop. Runs until max_cycles or interrupted."""
        self._recover_or_init()
        self.memory.record_event("agent_start", data={
            "mode": self.mode, "dry_run": self.dry_run,
            "max_cycles": self.max_cycles})
        print(f"[agent] Starting in {self.mode} mode "
              f"(dry_run={self.dry_run})")

        try:
            while True:
                if self.paused:
                    time.sleep(1)
                    continue

                self._cycle_count += 1
                if self.max_cycles and self._cycle_count > self.max_cycles:
                    print(f"[agent] Reached max cycles ({self.max_cycles})")
                    break

                print(f"\n[agent] === Cycle {self._cycle_count} ===")
                results = self._run_one_cycle()

                if results.get("handoffs", 0) > 0:
                    print(f"[agent] {results['handoffs']} handoff(s) "
                          f"requested — waiting for human")

                # Only a genuinely productive cycle (something executed,
                # not just rate-limited/skipped/handed-off) earns the
                # short 2s sleep. Anything else backs off to the full
                # cycle_delay so a stuck prospect can't be re-attempted
                # every couple of seconds forever.
                if results.get("productive", 0) == 0:
                    print(f"[agent] No productive work this cycle, sleeping "
                          f"{self.cycle_delay}s")
                    time.sleep(self.cycle_delay)
                else:
                    time.sleep(2)

        except KeyboardInterrupt:
            print("\n[agent] Interrupted by user")
        finally:
            self.memory.record_event("agent_stop",
                                      data={"cycles": self._cycle_count})
            print(f"[agent] Stopped after {self._cycle_count} cycles")
            if self.browser:
                try:
                    self.browser.close()
                except Exception as e:
                    print(f"[agent] Browser cleanup error: {e}")
            self._print_summary()

    def run_once(self) -> dict:
        """Run a single cycle. Returns the cycle results."""
        self._cycle_count += 1
        return self._run_one_cycle()

    def _recover_or_init(self):
        """On start: recover from last snapshot or initialize from store."""
        active = self.memory.tasks.active_tasks()
        if active:
            print(f"[agent] Recovered {len(active)} active task(s)")
            for t in active:
                p = self.memory.state.get_state(t["prospect_id"])
                name = (p.get("identity", {}).get("name")
                        or t["prospect_id"])
                print(f"  - {name}: {t['goal']} "
                      f"({len(t.get('completed', []))} done, "
                      f"{len(t.get('pending', []))} pending)")

    def _run_one_cycle(self) -> dict:
        """Execute one full cycle of the agent loop."""
        work_items = sweep_all(self.store)
        handoffs = 0
        work_done = 0
        productive = 0
        actions = []

        for item in work_items:
            prospect_id = item["prospect_id"]
            campaign_id = item["campaign_id"]
            campaign = item.get("campaign") or self.store.get_campaign(campaign_id)
            prospect = self.store.get_prospect(prospect_id)
            if not prospect or not campaign:
                continue

            # Skip prospects already waiting on a human. Re-planning them
            # every cycle achieves nothing but duplicate handoff events
            # and wasted planner calls - this was the main loop.
            last_handoff = self.memory.events.last_event_of_type(
                prospect_id, "handoff_requested")
            if last_handoff:
                cleared = self.memory.events.count_events(
                    prospect_id, "human_cleared")
                handoff_count = self.memory.events.count_events(
                    prospect_id, "handoff_requested")
                if handoff_count > cleared:
                    continue

            ctx = self.memory.get_prospect_context(prospect_id)
            name = (ctx.get("state", {}).get("identity", {}).get("name")
                    or prospect_id)
            print(f"[agent] Processing {name} "
                  f"(reason: {item['reason']})")

            plan = plan_next_action(
                self.memory, prospect, campaign,
                eligibility=item.get("eligibility_checks"),
                outreach_state=item.get("cp", {}).get("status"),
                model=self.model, base_url=self.base_url)

            print(f"[agent]   Plan: {plan.get('action')} — "
                  f"{plan.get('reason', '')}")

            # Circuit breaker: if the SAME action keeps getting proposed
            # for the SAME prospect cycle after cycle with nothing
            # changing in between, something is stuck (weak-model
            # confusion, a bug, a state the planner can't resolve).
            # Force a handoff instead of repeating it forever.
            streak_key = (prospect_id, plan.get("action"))
            if plan.get("action") not in ("wait", "skip", "handoff"):
                self._streak[streak_key] = self._streak.get(streak_key, 0) + 1
                for k in list(self._streak):
                    if k != streak_key and k[0] == prospect_id:
                        self._streak[k] = 0
                if self._streak[streak_key] >= self.STREAK_LIMIT:
                    print(f"  ! {plan.get('action')} proposed "
                          f"{self._streak[streak_key]}x in a row for "
                          f"{prospect_id} with no progress - forcing handoff")
                    self.memory.record_event(
                        "handoff_requested", prospect_id, campaign_id,
                        data={"reason": "loop_breaker",
                              "stuck_action": plan.get("action"),
                              "streak": self._streak[streak_key]})
                    self._streak[streak_key] = 0
                    handoffs += 1
                    continue
            else:
                self._streak[streak_key] = 0

            if self.dry_run:
                actions.append({"prospect_id": prospect_id,
                                "campaign_id": campaign_id,
                                "plan": plan, "executed": False})
                self.memory.record_event("dry_run", prospect_id, campaign_id,
                                         data={"action": plan.get("action"),
                                               "reason": plan.get("reason")})
                work_done += 1
                continue

            if self.mode == "manual" and plan.get("action") not in (
                    "wait", "skip"):
                action = plan.get("action")
                reason = plan.get("reason", "")
                print(f"\n[manual] Proposed action: {action}")
                print(f"[manual]   Reason: {reason}")
                if plan.get("params"):
                    print(f"[manual]   Params: {plan.get('params')}")
                print(f"[manual] Options: [y] execute  [n] skip  [h] handoff (human)")
                choice = input("[manual] Your choice: ").strip().lower()

                if choice == "y":
                    pass  # fall through to execute below
                elif choice == "h":
                    self.memory.record_event("handoff_requested",
                                             prospect_id, campaign_id,
                                             data={"reason": "manual_approval",
                                                   "plan": plan})
                    handoffs += 1
                    actions.append({"prospect_id": prospect_id,
                                    "campaign_id": campaign_id,
                                    "plan": plan, "executed": False,
                                    "handoff": True})
                    print("[manual]   -> Handoff recorded.")
                    continue
                else:
                    self.memory.record_event("agent_skip", prospect_id,
                                             campaign_id,
                                             data={"reason": "manual_skip",
                                                   "plan": plan})
                    actions.append({"prospect_id": prospect_id,
                                    "campaign_id": campaign_id,
                                    "plan": plan, "executed": False})
                    print("[manual]   -> Skipped.")
                    continue

            result = execute_plan(
                self.store, self.memory, self.browser,
                prospect, campaign, plan,
                model=self.model, base_url=self.base_url)

            actions.append({"prospect_id": prospect_id,
                            "campaign_id": campaign_id,
                            "plan": plan, "result": result,
                            "executed": True})
            work_done += 1

            status = result.get("status", "unknown")
            if status == "handed_off":
                handoffs += 1
            elif status not in ("rate_limited", "error", "skipped"):
                # Only count real progress - a rate-limited or errored
                # attempt is not progress, and treating it as such was
                # why the loop only ever slept 2s instead of backing off.
                productive += 1
                self._streak[streak_key] = 0

            self.memory.save_snapshot(prospect_id)

            print(f"[agent]   Result: {status}")
            if status == "error":
                print(f"[agent]   ERROR DETAIL: {result.get('error', 'no detail')}")

        return {
            "cycle": self._cycle_count,
            "work_items": len(work_items),
            "work_done": work_done,
            "productive": productive,
            "handoffs": handoffs,
            "actions": actions,
        }

    def _print_summary(self):
        print(f"\n[agent] Summary:")
        print(f"  Cycles: {self._cycle_count}")
        active = self.memory.tasks.active_tasks()
        print(f"  Active tasks: {len(active)}")
        pending = self.memory.events.conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'handoff_requested'"
        ).fetchone()[0]
        print(f"  Pending handoffs: {pending}")
