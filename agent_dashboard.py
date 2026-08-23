#!/usr/bin/env python3
"""Live dashboard for the LinkedIn outreach agent.

Runs the SAME AgentOrchestrator as `run_agent.py`, but in a background
thread, with a Tkinter window on top that:

  - Streams every event (agent_decision, action_executed, error,
    handoff_requested, ...) live as it's written, newest at the top.
  - Colour-codes rows so problems (errors, handoffs, loop-breaker stops)
    jump out instead of scrolling past in a terminal.
  - Lets you Pause / Resume the loop without killing the process.
  - Lists open handoffs and lets you clear one with a click (writes a
    `human_cleared` event) so the orchestrator picks that prospect back
    up on the next cycle - the "take action if needed" the loop needed.

It reads NOTHING from memory - every row comes straight from the
`events` table the orchestrator already writes to (see
linkedin_intelligence/automation/memory/event_store.py). This is the
"store everything, only look back for context" part: nothing new is
kept in RAM, the SQLite database is the single source of truth, and
this window is just a live tail + a couple of write actions on top
of it.

Usage:
  python agent_dashboard.py                # auto mode, live
  python agent_dashboard.py --manual        # require approval per action
  python agent_dashboard.py --dry-run       # plan only, no side effects
  python agent_dashboard.py --model llama3.1:8b  # use a stronger planner
"""

import argparse
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
import traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH
from linkedin_intelligence.automation.memory.memory_service import MemoryService
from linkedin_intelligence.automation.actions.browser_executor import BrowserExecutor
from linkedin_intelligence.agent.orchestrator import AgentOrchestrator

# ---------------------------------------------------------------------------
# Inline debugging: prints to stderr and appends to agent_debug.log
# (set AGENT_DEBUG=0 to disable)
# ---------------------------------------------------------------------------
_DEBUG_ON = os.environ.get("AGENT_DEBUG", "1") != "0"
_LOG_PATH = os.path.join(ROOT, "agent_debug.log")


def _dbg(msg: str):
    if not _DEBUG_ON:
        return
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        thread = threading.current_thread().name
        line = f"[{ts}] [dashboard] [{thread}] {msg}"
        print(line[:4000], file=sys.stderr, flush=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


ROW_COLORS = {
    "error": "#ffdede",
    "handoff_requested": "#fff3c4",
    "message_sent": "#dcffdc",
    "connection_sent": "#dcffdc",
    "agent_start": "#e0e0ff",
    "agent_stop": "#e0e0ff",
}


class Dashboard:
    def __init__(self, root, orchestrator, memory, poll_ms=1500):
        _dbg(f"Dashboard.__init__ poll_ms={poll_ms}")
        self.root = root
        self.orch = orchestrator
        self.memory = memory
        self.poll_ms = poll_ms
        self._last_event_id = 0

        root.title("LinkedIn Agent — Live Dashboard")
        root.geometry("1100x650")

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")

        self.status_var = tk.StringVar(value="starting…")
        ttk.Label(top, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).pack(side="left")

        self.pause_btn = ttk.Button(top, text="Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="right", padx=4)

        ttk.Label(top, text="  ").pack(side="right")
        self.cycle_var = tk.StringVar(value="cycle: 0")
        ttk.Label(top, textvariable=self.cycle_var).pack(side="right", padx=8)

        # --- live event stream ---
        mid = ttk.PanedWindow(root, orient="horizontal")
        mid.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(mid)
        mid.add(left, weight=3)

        cols = ("time", "prospect", "event", "detail")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=25)
        for c, w in zip(cols, (150, 140, 170, 500)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, side="left")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        vsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=vsb.set)
        for tag, color in ROW_COLORS.items():
            self.tree.tag_configure(tag, background=color)

        # --- handoffs needing a human ---
        right = ttk.Frame(mid, padding=(8, 0))
        mid.add(right, weight=1)
        ttk.Label(right, text="Open handoffs", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.handoff_list = tk.Listbox(right, height=25)
        self.handoff_list.pack(fill="both", expand=True, pady=4)
        ttk.Button(right, text="Clear selected (let agent retry)",
                   command=self._clear_selected).pack(fill="x")

        self._open_handoffs = []  # prospect_ids matching handoff_list rows

        self.root.after(200, self._poll)

    # ---- controls ----

    def _toggle_pause(self):
        self.orch.paused = not self.orch.paused
        self.pause_btn.config(text="Resume" if self.orch.paused else "Pause")

    def _clear_selected(self):
        sel = self.handoff_list.curselection()
        if not sel:
            return
        prospect_id = self._open_handoffs[sel[0]]
        self.memory.record_event("human_cleared", prospect_id,
                                  data={"note": "cleared from dashboard"})

    # ---- live polling (SQLite is the only shared state - safe to poll
    #      from the Tk thread while the orchestrator thread writes) ----

    def _poll(self):
        try:
            self._refresh_events()
            self._refresh_handoffs()
            mode = "PAUSED" if self.orch.paused else "running"
            self.status_var.set(f"agent: {mode}")
            self.cycle_var.set(f"cycle: {self.orch._cycle_count}")
        except Exception as e:
            _dbg(f"EXCEPTION _poll: {e}\n{traceback.format_exc()}")
            self.status_var.set(f"dashboard error: {e}")
        self.root.after(self.poll_ms, self._poll)

    def _refresh_events(self):
        rows = self.memory.events.conn.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT 200",
            (self._last_event_id,)).fetchall()
        for r in rows:
            d = dict(r)
            self._last_event_id = max(self._last_event_id, d["id"])
            try:
                data = json.loads(d.get("data_json") or "{}")
            except json.JSONDecodeError:
                data = {}
            detail = ", ".join(f"{k}={v}" for k, v in list(data.items())[:4])
            tag = d["event_type"] if d["event_type"] in ROW_COLORS else ""
            self.tree.insert("", 0, values=(
                d["created_at"], d.get("prospect_id") or "-",
                d["event_type"], detail[:180]), tags=(tag,))
        # keep the tree from growing unbounded in the UI
        children = self.tree.get_children()
        if len(children) > 500:
            for item in children[500:]:
                self.tree.delete(item)

    def _refresh_handoffs(self):
        rows = self.memory.events.conn.execute(
            """SELECT prospect_id, COUNT(*) as n FROM events
               WHERE event_type = 'handoff_requested' AND prospect_id IS NOT NULL
               GROUP BY prospect_id"""
        ).fetchall()
        self.handoff_list.delete(0, tk.END)
        self._open_handoffs = []
        for r in rows:
            pid = r["prospect_id"]
            cleared = self.memory.events.count_events(pid, "human_cleared")
            requested = r["n"]
            if requested > cleared:
                self.handoff_list.insert(tk.END, f"{pid}  ({requested - cleared} pending)")
                self._open_handoffs.append(pid)


def main():
    parser = argparse.ArgumentParser(description="Live dashboard for the LinkedIn agent")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--model", type=str, default="gemma4:31b-cloud",
                        help="Planner model. The shipped default (qwen3.5:0.8b) "
                             "is tiny and prone to flaky JSON/looping — a bigger "
                             "model here is the single highest-leverage fix.")
    parser.add_argument("--browser-model", type=str, default="gemma4:31b-cloud")
    parser.add_argument("--base-url", type=str, default="http://localhost:11434")
    parser.add_argument("--db", type=str, default=None)
    parser.add_argument("--delay", type=int, default=60)
    args = parser.parse_args()

    db_path = args.db or DB_PATH
    _dbg(f"main() starting db={db_path} model={args.model} browser_model={args.browser_model} "
         f"dry_run={args.dry_run} manual={args.manual}")
    store = Store(db_path)
    memory = MemoryService(db_path)
    browser = BrowserExecutor(model=args.browser_model, base_url=args.base_url)

    orchestrator = AgentOrchestrator(
        store=store, memory=memory, browser=browser,
        model=args.model, base_url=args.base_url,
        mode="manual" if args.manual else "auto",
        dry_run=args.dry_run, cycle_delay=args.delay,
    )

    def run_loop():
        try:
            _dbg("orchestrator thread starting")
            orchestrator.run()
            _dbg("orchestrator thread finished normally")
        except Exception as e:
            _dbg(f"EXCEPTION orchestrator thread crashed: {e}\n{traceback.format_exc()}")
            print(f"[dashboard] orchestrator crashed: {e}")

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        style.theme_use("clam")
    except Exception:
        pass
    Dashboard(root, orchestrator, memory)

    def on_close():
        _dbg("on_close: shutting down dashboard")
        orchestrator.max_cycles = orchestrator._cycle_count  # stop after this cycle
        orchestrator.paused = False
        root.destroy()
        os._exit(0)  # browser/threads shouldn't hang the process on close

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
