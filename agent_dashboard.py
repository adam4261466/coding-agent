#!/usr/bin/env python3
"""NEXUS workstation - control room for the permission-gated LinkedIn agent.

The dashboard is a VIEW over the store + memory. It edits exactly one
decision surface (per-prospect permissions) and watches the operator run.

Layout:
  LEFT   - PROSPECT QUEUE (allowed contact / manual review / blocked people)
  RIGHT  - CURRENT PERSON panel (pipeline + permissions) + LIVE AGENT log

Usage:
    python agent_dashboard.py
    python agent_dashboard.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import traceback
from datetime import datetime

import tkinter as tk
from tkinter import messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from linkedin_intelligence.store import Store
from linkedin_intelligence.utils import DB_PATH
from linkedin_intelligence.automation.memory.memory_service import MemoryService
from linkedin_intelligence.agent.operator import AgentOperator
from linkedin_intelligence.agent.permissions import (
    PERMISSION_FLAGS, normalize)

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


# ============================================================================
# THEME
# ============================================================================

BG = "#070B12"
PANEL = "#0D1420"
PANEL_2 = "#111B29"
BORDER = "#1D2B3D"

TEXT = "#E6EEF8"
MUTED = "#72839A"

CYAN = "#35D8FF"
GREEN = "#58E6A8"
YELLOW = "#FFD166"
ORANGE = "#FF9F43"
RED = "#FF5D73"
PURPLE = "#9B8CFF"
BLUE = "#5EA8FF"

FONT = "Segoe UI"
MONO = "Cascadia Mono"

BUCKET_STYLE = {
    "allowed": ("ALLOWED CONTACT", GREEN),
    "manual": ("MANUAL REVIEW", YELLOW),
    "blocked": ("BLOCKED PEOPLE", RED),
}

BOX_EMPTY = "\u2610"
BOX_CHECKED = "\u2611"
BOX_PARTIAL = "\u25a3"

FLAG_LABELS = {
    "view_profile": "View profiles",
    "send_connection": "Connect",
    "send_message": "Message",
    "reply": "Reply",
    "follow_up": "Follow up",
}


# ============================================================================
# WORKSTATION APP
# ============================================================================

class Workstation:

    def __init__(self, root: tk.Tk, db_path: str, dry_run: bool):
        self.root = root
        self.db_path = db_path
        self.dry_run = dry_run

        self.store = Store(db_path)
        self.memory = MemoryService(db_path)
        self.operator = AgentOperator(
            store=self.store, memory=self.memory,
            base_url="http://localhost:11434", dry_run=dry_run,
            cycle_delay=60)

        self._agent_thread = None
        self._running = False
        self._selected_pid = None
        self._queue_entries = {}
        self._checked = set()
        self._loading_perms = False

        self._build_style()
        self._build_layout()

        self.root.after(1000, self._refresh_loop)
        self._refresh_queue()
        self._load_recent_log()

    # ---- style ----

    def _build_style(self):
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("Treeview", background=PANEL, foreground=TEXT,
                    fieldbackground=PANEL, bordercolor=BORDER,
                    rowheight=26, font=(FONT, 10))
        s.configure("Treeview.Heading", background=PANEL_2,
                    foreground=MUTED, bordercolor=BORDER,
                    font=(FONT, 9, "bold"))
        s.map("Treeview", background=[("selected", "#16324A")],
              foreground=[("selected", TEXT)])
        s.configure("TCheckbutton", background=PANEL, foreground=TEXT,
                    focuscolor=PANEL)
        s.map("TCheckbutton", background=[("active", PANEL)])

    # ---- layout ----

    def _build_layout(self):
        self.root.title("NEXUS - LinkedIn Agent Workstation")
        self.root.configure(bg=BG)
        self.root.geometry("1420x860")
        self.root.minsize(1150, 700)

        header = tk.Frame(self.root, bg=PANEL, height=64)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)

        tk.Label(header, text="NEXUS", font=(FONT, 20, "bold"),
                 fg=CYAN, bg=PANEL).pack(side=tk.LEFT, padx=(18, 6), pady=10)
        tk.Label(header, text="AGENT WORKSTATION", font=(FONT, 10),
                 fg=MUTED, bg=PANEL).pack(side=tk.LEFT, pady=14)

        self.status_label = tk.Label(header, text="AGENT STOPPED",
                                     font=(MONO, 11, "bold"), fg=RED,
                                     bg=PANEL)
        self.status_label.pack(side=tk.RIGHT, padx=18)

        self.start_btn = tk.Button(
            header, text="START AGENT", command=self._toggle_agent,
            font=(FONT, 11, "bold"), fg=BG, bg=GREEN, relief=tk.FLAT,
            padx=18, pady=4, cursor="hand2", activebackground="#7BF0C1")
        self.start_btn.pack(side=tk.RIGHT, padx=8)

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True)
        main.grid_columnconfigure(0, weight=2)
        main.grid_columnconfigure(1, weight=3)
        main.grid_rowconfigure(0, weight=1)

        left = tk.Frame(main, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        right = tk.Frame(main, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        right.grid_rowconfigure(0, weight=0)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self._build_queue_panel(left)
        self._build_person_panel(right)
        self._build_log_panel(right)

    def _build_queue_panel(self, parent):
        wrap = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                        highlightbackground=BORDER)
        wrap.pack(fill=tk.BOTH, expand=True)

        head = tk.Frame(wrap, bg=PANEL_2, height=40)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        tk.Label(head, text="PROSPECT QUEUE", font=(FONT, 11, "bold"),
                 fg=TEXT, bg=PANEL_2).pack(side=tk.LEFT, padx=12)
        tk.Label(head, text="SEARCH:", font=(FONT, 9), fg=MUTED,
                 bg=PANEL_2).pack(side=tk.LEFT, padx=(6, 2))
        self.search_var = tk.StringVar()
        search_entry = tk.Entry(head, textvariable=self.search_var,
                                font=(FONT, 9), fg=TEXT, bg=PANEL_2,
                                relief=tk.FLAT, insertbackground=TEXT)
        search_entry.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        search_entry.bind("<KeyRelease>", self._on_search_change)
        self.queue_summary_label = tk.Label(head, text="", font=(MONO, 9),
                                            fg=MUTED, bg=PANEL_2)
        self.queue_summary_label.pack(side=tk.RIGHT, padx=12)

        bulk = tk.Frame(wrap, bg=PANEL_2)
        bulk.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Label(bulk, text="BULK:", font=(FONT, 9, "bold"), fg=MUTED,
                 bg=PANEL_2).pack(side=tk.LEFT, padx=(12, 4))
        self.bulk_label = tk.Label(bulk, text="0 selected",
                                   font=(MONO, 9), fg=CYAN, bg=PANEL_2)
        self.bulk_label.pack(side=tk.LEFT)
        self.bulk_allow = tk.Button(
            bulk, text="ALLOW (with shown flags)",
            command=lambda: self._bulk_apply("allowed"),
            font=(FONT, 9, "bold"), fg=BG, bg=GREEN, relief=tk.FLAT,
            padx=8, state=tk.DISABLED, cursor="hand2")
        self.bulk_allow.pack(side=tk.LEFT, padx=6, pady=5)
        self.bulk_manual = tk.Button(
            bulk, text="MANUAL", command=lambda: self._bulk_apply("manual"),
            font=(FONT, 9, "bold"), fg=BG, bg=YELLOW, relief=tk.FLAT,
            padx=8, state=tk.DISABLED, cursor="hand2")
        self.bulk_manual.pack(side=tk.LEFT, padx=6)
        self.bulk_block = tk.Button(
            bulk, text="BLOCK", command=lambda: self._bulk_apply("blocked"),
            font=(FONT, 9, "bold"), fg=BG, bg=RED, relief=tk.FLAT,
            padx=8, state=tk.DISABLED, cursor="hand2")
        self.bulk_block.pack(side=tk.LEFT, padx=6)
        self.bulk_view = tk.Button(
            bulk, text="VIEW PROFILE", command=lambda: self._bulk_apply("view_profile"),
            font=(FONT, 9, "bold"), fg=BG, bg=CYAN, relief=tk.FLAT,
            padx=8, state=tk.DISABLED, cursor="hand2")
        self.bulk_view.pack(side=tk.LEFT, padx=6, pady=5)
        self.bulk_message = tk.Button(
            bulk, text="MESSAGE", command=lambda: self._bulk_apply("send_message"),
            font=(FONT, 9, "bold"), fg=BG, bg=BLUE, relief=tk.FLAT,
            padx=8, state=tk.DISABLED, cursor="hand2")
        self.bulk_message.pack(side=tk.LEFT, padx=6)
        self.bulk_reply = tk.Button(
            bulk, text="REPLY", command=lambda: self._bulk_apply("reply"),
            font=(FONT, 9, "bold"), fg=BG, bg=ORANGE, relief=tk.FLAT,
            padx=8, state=tk.DISABLED, cursor="hand2")
        self.bulk_reply.pack(side=tk.LEFT, padx=6, pady=5)
        self.bulk_followup = tk.Button(
            bulk, text="FOLLOW UP", command=lambda: self._bulk_apply("follow_up"),
            font=(FONT, 9, "bold"), fg=BG, bg=YELLOW, relief=tk.FLAT,
            padx=8, state=tk.DISABLED, cursor="hand2")
        self.bulk_followup.pack(side=tk.LEFT, padx=6)
        tk.Label(bulk, text="(ALLOW copies the flag ticks shown in "
                            "CURRENT PERSON)",
                 font=(FONT, 8), fg=MUTED, bg=PANEL_2).pack(side=tk.LEFT,
                                                             padx=8)

        cols = ("sel", "dot", "person", "company", "next")
        self.queue_tree = ttk.Treeview(wrap, columns=cols,
                                       show="tree headings",
                                       selectmode="browse")
        self.queue_tree.heading("#0", text="")
        self.queue_tree.column("#0", width=0, stretch=False)
        self.queue_tree.heading(
            "sel", text=BOX_EMPTY, command=self._toggle_all_checks)
        self.queue_tree.column("sel", width=34, anchor=tk.CENTER,
                               stretch=False)
        self.queue_tree.heading("dot", text="")
        self.queue_tree.column("dot", width=36, anchor=tk.CENTER,
                               stretch=False)
        self.queue_tree.heading("person", text="NAME")
        self.queue_tree.column("person", width=170)
        self.queue_tree.heading("company", text="COMPANY")
        self.queue_tree.column("company", width=130)
        self.queue_tree.heading("next", text="NEXT ACTION")
        self.queue_tree.column("next", width=140)
        self.queue_tree.tag_configure("bucket_allowed", foreground=GREEN)
        self.queue_tree.tag_configure("bucket_manual", foreground=YELLOW)
        self.queue_tree.tag_configure("bucket_blocked", foreground=RED)
        self.queue_tree.tag_configure("current", background="#16324A")

        vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL,
                            command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=vsb.set)
        self.queue_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                             padx=(6, 0), pady=6)
        vsb.pack(side=tk.LEFT, fill=tk.Y, pady=6, padx=(0, 6))
        self.queue_tree.bind("<<TreeviewSelect>>", self._on_select_person)
        self.queue_tree.bind("<Button-1>", self._on_queue_click)

    # ---- bulk selection ----

    def _on_queue_click(self, event):
        tree = self.queue_tree
        region = tree.identify_region(event.x, event.y)
        if region == "heading":
            if tree.identify_column(event.x) == "#1":
                # "break" suppresses the class binding that would fire the
                # heading command too - so invoke it here, exactly once.
                self._toggle_all_checks()
                return "break"
            return None
        if region != "cell":
            return None
        if tree.identify_column(event.x) != "#1":
            return None
        iid = tree.identify_row(event.y)
        if not iid:
            return None
        if iid.startswith("bucket:"):
            key = iid.split(":", 1)[1]
            kids = [c.split(":", 1)[1]
                    for c in tree.get_children(f"bucket:{key}")]
            all_checked = bool(kids) and \
                all(p in self._checked for p in kids)
            if all_checked:
                self._checked -= set(kids)
            else:
                self._checked |= set(kids)
        else:
            pid = iid.split(":", 1)[1]
            if pid in self._checked:
                self._checked.discard(pid)
            else:
                self._checked.add(pid)
        self._sync_check_glyphs()
        return "break"

    def _toggle_all_checks(self):
        all_pids = {e["prospect"]["prospect_id"]
                    for e in self._queue_entries.values()}
        if self._checked and self._checked >= all_pids:
            self._checked.clear()
        else:
            self._checked |= all_pids
        self._sync_check_glyphs()

    def _sync_check_glyphs(self):
        tree = self.queue_tree
        total = len(self._queue_entries)
        for key in ("allowed", "manual", "blocked"):
            kids = [c.split(":", 1)[1]
                    for c in tree.get_children(f"bucket:{key}")]
            n = sum(1 for p in kids if p in self._checked)
            glyph = BOX_EMPTY
            if kids and n == len(kids):
                glyph = BOX_CHECKED
            elif 0 < n < len(kids):
                glyph = BOX_PARTIAL
            try:
                tree.set(f"bucket:{key}", "sel", glyph)
            except tk.TclError:
                pass
        tree.heading("sel", text=BOX_CHECKED
                     if total and len(self._checked) >= total else BOX_EMPTY)
        self.bulk_label.config(text=f"{len(self._checked)} selected")
        state = tk.NORMAL if self._checked else tk.DISABLED
        for btn in (self.bulk_allow, self.bulk_manual, self.bulk_block,
                self.bulk_view, self.bulk_message,
                self.bulk_reply, self.bulk_followup):
            btn.config(state=state)

    def _bulk_apply(self, level: str):
        pids = sorted(self._checked)
        if not pids:
            return
        if not messagebox.askyesno(
                "Bulk permissions",
                f"Set {level.upper()} for {len(pids)} people?"):
            return
        flags = None
        if level == "allowed":
            flags = {f: v.get() for f, v in self.flag_vars.items()}
        elif level == "view_profile":
            flags = {"view_profile": True}
        elif level == "send_message":
            flags = {"send_message": True}
        elif level == "reply":
            flags = {"reply": True}
        elif level == "follow_up":
            flags = {"follow_up": True}
        for pid in pids:
            record = self.store.set_permission(pid, permission=level,
                                               flags=flags)
            self.store.set_status(pid, record["permission"])
            self.memory.record_event(
                "permissions_changed", pid,
                data={"permission": level, "bulk": True, "flags": flags},
                source="human")
        self.perm_status.config(
            text=f"bulk {level}: {len(pids)} people at "
                 f"{datetime.now().strftime('%H:%M:%S')}", fg=GREEN)
        self._checked.clear()
        self._refresh_queue()
        entry = self._find_entry(self._selected_pid) \
            if self._selected_pid else None
        if entry:
            self._render_person(entry)

    def _build_person_panel(self, parent):
        wrap = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                        highlightbackground=BORDER)
        wrap.grid(row=0, column=0, sticky="nsew")

        head = tk.Frame(wrap, bg=PANEL_2, height=40)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        tk.Label(head, text="CURRENT PERSON", font=(FONT, 11, "bold"),
                 fg=TEXT, bg=PANEL_2).pack(side=tk.LEFT, padx=12)
        self.current_marker = tk.Label(head, text="", font=(MONO, 9),
                                       fg=CYAN, bg=PANEL_2)
        self.current_marker.pack(side=tk.RIGHT, padx=12)

        ident = tk.Frame(wrap, bg=PANEL)
        ident.pack(fill=tk.X, padx=14, pady=(12, 4))
        self.p_name = tk.Label(ident, text="-", font=(FONT, 17, "bold"),
                               fg=TEXT, bg=PANEL, anchor="w")
        self.p_name.pack(anchor="w")
        self.p_meta = tk.Label(ident, text="select someone in the queue",
                               font=(FONT, 10), fg=MUTED, bg=PANEL,
                               anchor="w")
        self.p_meta.pack(anchor="w")

        pipe_wrap = tk.Frame(wrap, bg=PANEL)
        pipe_wrap.pack(fill=tk.X, padx=14, pady=(10, 6))
        tk.Label(pipe_wrap, text="PIPELINE", font=(FONT, 9, "bold"),
                 fg=MUTED, bg=PANEL).pack(anchor="w")
        self.pipeline_frame = tk.Frame(pipe_wrap, bg=PANEL)
        self.pipeline_frame.pack(anchor="w", pady=4)

        perms_wrap = tk.Frame(wrap, bg=PANEL)
        perms_wrap.pack(fill=tk.X, padx=14, pady=(4, 10))

        tk.Label(perms_wrap, text="PERMISSIONS  (human decides once - "
                                  "the agent only obeys)",
                 font=(FONT, 9, "bold"), fg=MUTED,
                 bg=PANEL).pack(anchor="w", pady=(0, 4))

        level_row = tk.Frame(perms_wrap, bg=PANEL)
        level_row.pack(anchor="w")
        tk.Label(level_row, text="status:", font=(FONT, 10), fg=MUTED,
                 bg=PANEL).pack(side=tk.LEFT)
        self.level_var = tk.StringVar(value="manual")
        for level, color in (("allowed", GREEN), ("manual", YELLOW),
                             ("blocked", RED)):
            rb = tk.Radiobutton(level_row, text=level.upper(),
                                variable=self.level_var, value=level,
                                command=self._save_permissions,
                                font=(FONT, 10, "bold"), fg=color,
                                bg=PANEL, selectcolor=PANEL_2,
                                activebackground=PANEL,
                                highlightthickness=0, cursor="hand2")
            rb.pack(side=tk.LEFT, padx=(10, 0))

        flags_row = tk.Frame(perms_wrap, bg=PANEL)
        flags_row.pack(anchor="w", pady=(6, 0))
        self.flag_vars = {}
        for flag in PERMISSION_FLAGS:
            var = tk.BooleanVar(value=False)
            cb = tk.Checkbutton(flags_row, text=FLAG_LABELS[flag],
                                variable=var, command=self._save_permissions,
                                font=(FONT, 10), fg=TEXT, bg=PANEL,
                                selectcolor=PANEL_2, activebackground=PANEL,
                                highlightthickness=0, cursor="hand2")
            cb.pack(side=tk.LEFT, padx=(0, 12))
            self.flag_vars[flag] = var

        notes_row = tk.Frame(perms_wrap, bg=PANEL)
        notes_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(notes_row, text="notes:", font=(FONT, 10), fg=MUTED,
                 bg=PANEL).pack(side=tk.LEFT)
        self.notes_var = tk.StringVar()
        notes_entry = tk.Entry(notes_row, textvariable=self.notes_var,
                               font=(FONT, 10), fg=TEXT, bg=PANEL_2,
                               insertbackground=TEXT, relief=tk.FLAT)
        notes_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        notes_entry.bind("<Return>", lambda e: self._save_permissions())

        save_btn = tk.Button(perms_wrap, text="SAVE PERMISSIONS",
                             command=self._save_permissions,
                             font=(FONT, 10, "bold"), fg=BG, bg=CYAN,
                             relief=tk.FLAT, padx=12, cursor="hand2",
                             activebackground="#7BE4FF")
        save_btn.pack(anchor="w", pady=(8, 0))
        self.perm_status = tk.Label(perms_wrap, text="", font=(MONO, 8),
                                    fg=MUTED, bg=PANEL)
        self.perm_status.pack(anchor="w", pady=(2, 0))

    def _build_log_panel(self, parent):
        wrap = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                        highlightbackground=BORDER)
        wrap.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

        head = tk.Frame(wrap, bg=PANEL_2, height=40)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        tk.Label(head, text="LIVE AGENT", font=(FONT, 11, "bold"),
                 fg=TEXT, bg=PANEL_2).pack(side=tk.LEFT, padx=12)
        tk.Label(head, text="timestamped operator log",
                 font=(FONT, 9), fg=MUTED,
                 bg=PANEL_2).pack(side=tk.LEFT, padx=(8, 0))
        self.log_clock = tk.Label(head, text="", font=(MONO, 9),
                                  fg=MUTED, bg=PANEL_2)
        self.log_clock.pack(side=tk.RIGHT, padx=12)

        self.log_text = tk.Text(wrap, bg=PANEL, fg=TEXT, wrap="word",
                                font=(MONO, 10), relief=tk.FLAT,
                                state=tk.DISABLED, padx=10, pady=8)
        log_vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL,
                                command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_vsb.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                           padx=(6, 0), pady=6)
        log_vsb.pack(side=tk.LEFT, fill=tk.Y, pady=6, padx=(0, 6))
        for kind, color in (("WORK", CYAN), ("DONE", GREEN),
                            ("GATE", ORANGE), ("HOLD", YELLOW),
                            ("RATE", ORANGE), ("ERROR", RED)):
            self.log_text.tag_configure(kind, foreground=color)

    # ---- queue ----

    def _refresh_queue(self):
        try:
            buckets = self.operator.queue.buckets()
        except Exception as exc:
            _dbg(f"refresh_queue failed: {exc}\n{traceback.format_exc()}")
            return

        tree = self.queue_tree
        selected = self._selected_pid
        tree.delete(*tree.get_children())
        self._queue_entries = {}

        search_text = self.search_var.get().lower()

        total = sum(len(v) for v in buckets.values())
        displayed = 0
        self.queue_summary_label.config(
            text=f"{len(buckets['allowed'])} allowed / "
                 f"{len(buckets['manual'])} review / "
                 f"{len(buckets['blocked'])} blocked / {total} total")

        live_pids = set()
        for key in ("allowed", "manual", "blocked"):
            title, color = BUCKET_STYLE[key]
            parent = tree.insert("", "end", iid=f"bucket:{key}",
                                 open=True, text="",
                                 values=(BOX_EMPTY,
                                         f"{title} ({len(buckets[key])})",
                                         "", ""),
                                 tags=(f"bucket_{key}",))
            for i, entry in enumerate(buckets[key]):
                pid = entry["prospect"]["prospect_id"]
                full_name = entry["prospect"].get("full_name") or ""
                if search_text and search_text not in full_name.lower():
                    continue
                live_pids.add(pid)
                record = normalize(entry["record"])
                nxt = self._next_action_label(key, entry)
                dot = "\u25cf" if key == "allowed" else \
                    ("\u25d1" if key == "manual" else "\u2715")
                iid = f"{key}:{pid}"
                self._queue_entries[iid] = entry
                tree.insert(parent, "end", iid=iid,
                            values=(BOX_CHECKED if pid in self._checked
                                    else BOX_EMPTY,
                                    dot,
                                    full_name,
                                    entry["prospect"].get("current_company")
                                    or "-",
                                    nxt),
                            tags=("current",) if pid == selected else ())
                displayed += 1
        self._checked &= live_pids
        self._sync_check_glyphs()
        if selected:
            self._show_current_running(selected)
        self.queue_summary_label.config(
            text=f"{displayed} of {total} shown")

    def _next_action_label(self, bucket_key: str, entry) -> str:
        if bucket_key == "blocked":
            return "do not contact"
        if bucket_key == "manual":
            return "needs your decision"
        try:
            step = self.operator.plan_step(entry["prospect"], entry["record"])
        except Exception:
            step = None
        if not step:
            return "waiting"
        return {
            "observe_profile": "observe profile",
            "send_connection_request": "connect",
            "send_message": "first message",
            "reply": "handle reply",
            "follow_up": "follow up",
        }.get(step["action"], step["action"])

    def _on_search_change(self, event=None):
        self._refresh_queue()

    def _on_select_person(self, _event=None):
        sel = self.queue_tree.selection()
        if not sel or sel[0].startswith("bucket:"):
            return
        entry = self._queue_entries.get(sel[0])
        if not entry:
            return
        self._selected_pid = entry["prospect"]["prospect_id"]
        self._render_person(entry)

    def _show_current_running(self, pid: str):
        pass

    # ---- person panel ----

    def _render_person(self, entry):
        p = entry["prospect"]
        record = normalize(entry["record"])
        pid = p["prospect_id"]

        self.p_name.config(text=p.get("full_name") or pid)
        meta = []
        if p.get("raw_position"):
            meta.append(p["raw_position"])
        if p.get("current_company"):
            meta.append(p["current_company"])
        conn = p.get("connection_status") or "?"
        meta.append(f"[{conn}]")
        level_color = {"allowed": GREEN, "manual": YELLOW,
                       "blocked": RED}[record["permission"]]
        self.p_meta.config(text="  |  ".join(meta) if meta else "-")
        self.current_marker.config(text=record["permission"].upper(),
                                   fg=level_color)

        self._loading_perms = True
        self.level_var.set(record["permission"])
        for flag in PERMISSION_FLAGS:
            self.flag_vars[flag].set(bool(record[flag]))
        self.notes_var.set(record.get("notes") or "")
        self._loading_perms = False

        self._render_pipeline(pid, record)

    def _render_pipeline(self, pid: str, record):
        for w in self.pipeline_frame.winfo_children():
            w.destroy()

        try:
            ctx = self.memory.get_prospect_context(pid)
        except Exception:
            ctx = {}
        rel = (ctx.get("state") or {}).get("relationship") or {}
        recent = ctx.get("recent_events") or []

        def count(t):
            return sum(1 for e in recent if e.get("type") == t)

        observed = count("profile_observed") > 0
        connected = (rel.get("connection_status") == "connected") or \
            count("connection_accepted") > 0
        messages = count("message_sent")
        replies = count("reply_detected")
        replies_done = count("reply_processed")
        follow_ups = sum(1 for m in self.store.messages_for(prospect_id=pid)
                         if m.get("status") in ("approved_to_send",)) and \
            messages > 1

        conn_ok = connected or count("connection_sent") > 0
        stages = [
            ("PROFILE", observed,
             bool(record["view_profile"]),
             "observed" if observed else "not yet"),
            ("MESSAGE", messages > 0,
             bool(record["send_message"]) and conn_ok,
             "sent" if messages else
             ("awaiting connection" if not conn_ok else "pending")),
            ("REPLY", replies_done >= max(replies, 1) and replies > 0,
             bool(record["reply"]),
             f"{replies_done}/{max(replies, 0)} handled"),
            ("FOLLOW-UP", bool(follow_ups),
             bool(record["follow_up"]) and messages > 0,
             "done" if follow_ups else "standing by"),
        ]

        for i, (label, done, unlocked, note) in enumerate(stages):
            if done:
                bg, fg, glyph = "#12331F", GREEN, "\u2713"
            elif not unlocked:
                bg, fg, glyph = PANEL_2, MUTED, "\u00b7"
            else:
                bg, fg, glyph = "#0E2E42", CYAN, "\u25cb"
            chip = tk.Frame(self.pipeline_frame, bg=bg, padx=10, pady=6,
                            highlightthickness=1,
                            highlightbackground=BORDER)
            chip.grid(row=0, column=i * 2, padx=3, sticky="w")
            tk.Label(chip, text=f"{glyph} {label}", font=(FONT, 10, "bold"),
                     fg=fg, bg=bg).pack()
            tk.Label(chip, text=note, font=(MONO, 8), fg=MUTED,
                     bg=bg).pack()
            if i < len(stages) - 1:
                tk.Label(self.pipeline_frame, text="\u2192", font=(FONT, 11),
                         fg=BORDER, bg=PANEL).grid(row=0, column=i * 2 + 1,
                                                   padx=1)

    # ---- permissions editing ----

    def _save_permissions(self):
        if self._loading_perms or not self._selected_pid:
            return
        pid = self._selected_pid
        try:
            record = self.store.set_permission(
                pid,
                permission=self.level_var.get(),
                flags={f: v.get() for f, v in self.flag_vars.items()},
                notes=self.notes_var.get().strip() or None)
            self.store.set_status(pid, record["permission"])
            self.memory.record_event(
                "permissions_changed", pid,
                data={"permission": record["permission"],
                      "flags": {f: bool(record[f]) for f in PERMISSION_FLAGS}},
                source="human")
            self.perm_status.config(
                text=f"saved {datetime.now().strftime('%H:%M:%S')}",
                fg=GREEN)
            self._refresh_queue()
            entry = self._find_entry(pid)
            if entry:
                self._render_person(entry)
        except Exception as exc:
            _dbg(f"save_permissions failed: {exc}\n{traceback.format_exc()}")
            messagebox.showerror("Permissions", f"Could not save:\n{exc}")

    def _find_entry(self, pid: str):
        for entry in self._queue_entries.values():
            if entry["prospect"]["prospect_id"] == pid:
                return entry
        return None

    # ---- live log ----

    def _append_log(self, line: str):
        kind = None
        for tag in ("WORK", "DONE", "GATE", "HOLD", "RATE", "ERROR"):
            if f"] {tag} " in line or line.startswith(f"[{tag}") or \
                    f" {tag} " in line.split("] ", 1)[-1][:6]:
                kind = tag
                break
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n", (kind,) if kind else ())
        if int(self.log_text.index("end-1c").split(".")[0]) > 800:
            self.log_text.delete("1.0", "200.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _load_recent_log(self):
        try:
            events = self.memory.events.events_for(limit=30)
        except Exception:
            events = []
        for e in events:
            ts = str(e.get("created_at", ""))[11:19]
            data = e.get("data") or {}
            detail = data.get("action") or data.get("reason") or ""
            self._append_log(f"[{ts}] {e.get('event_type')} {detail}"
                             .strip())

    # ---- agent controls ----

    def _toggle_agent(self):
        if self._running:
            self.operator.paused = True
            self._running = False
            self.start_btn.config(text="START AGENT", bg=GREEN)
            self.status_label.config(text="AGENT PAUSED", fg=YELLOW)
            self._append_log(f"[{datetime.now().strftime('%H:%M:%S')}] "
                             f"paused by human")
        else:
            if self._agent_thread and self._agent_thread.is_alive():
                self.operator.paused = False
            else:
                self._agent_thread = threading.Thread(
                    target=self._run_operator, name="operator",
                    daemon=True)
                self._agent_thread.start()
            self._running = True
            self.start_btn.config(text="PAUSE", bg=YELLOW)
            self.status_label.config(text="AGENT RUNNING", fg=GREEN)

    def _run_operator(self):
        try:
            self.operator.run()
        except Exception as exc:
            _dbg(f"operator crashed: {exc}\n{traceback.format_exc()}")
            self.root.after(0, lambda: messagebox.showerror(
                "Agent", f"Operator crashed:\n{exc}"))

    # ---- periodic refresh ----

    def _refresh_loop(self):
        try:
            seen = len(self.operator.recent_log())
            lines = self.operator.recent_log()
            if not hasattr(self, "_last_log_len"):
                self._last_log_len = 0
            if seen != self._last_log_len:
                self._last_log_len = seen
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.delete("1.0", tk.END)
                self.log_text.configure(state=tk.DISABLED)
                for line in lines:
                    self._append_log(line)
            self.log_clock.config(
                text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self._refresh_queue()
        except Exception as exc:
            _dbg(f"refresh_loop error: {exc}")
        finally:
            self.root.after(3000, self._refresh_loop)


def main():
    parser = argparse.ArgumentParser(
        description="NEXUS workstation for the LinkedIn agent")
    parser.add_argument("--db", type=str, default=None,
                        help="Database path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run the agent in dry-run mode")
    args = parser.parse_args()

    db_path = args.db or DB_PATH
    print(f"[dashboard] Database: {db_path}")

    root = tk.Tk()
    try:
        app = Workstation(root, db_path, dry_run=args.dry_run)
    except Exception:
        traceback.print_exc()
        raise
    root.protocol("WM_DELETE_WINDOW", app.root.destroy)
    root.mainloop()
    try:
        app.memory.close()
        app.store.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
