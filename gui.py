import tkinter as tk
from tkinter import scrolledtext, ttk
import threading
import json
import os
import sys
import time
import requests

from agent import SYSTEM_PROMPT

from linkedin_intelligence.utils import DB_PATH, campaign_config
from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach.approval import approve, reject, approval_queue
from linkedin_intelligence.outreach.pipeline import produce_message, register_reply, produce_batch
from linkedin_intelligence.outreach.sequence import mark_sent
from linkedin_intelligence.outreach.campaign import sync_campaigns, eligible_for_campaign, assign_prospects
from linkedin_intelligence.outreach import analytics

STATUS_FILTERS = {
    "Ready for review": "ready_for_human_review",
    "Research": "research",
    "Follow up": "follow_up",
    "New": "new",
    "Skipped": "skipped",
    "Do not contact": "do_not_contact",
    "All": None,
}


class AgentGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Coding Agent - Prospecting")
        self.root.geometry("980x700")
        self.root.configure(bg="#1a1a2e")
        self.root.minsize(640, 400)

        self.base_url = "http://localhost:11434"
        self.model = "gemma4:31b-cloud"
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT.format(cwd=os.getcwd())}]
        self.running = False
        self.review_prospects = []
        self.selected_pid = None
        self.outreach_messages = []
        self.selected_msg_id = None
        self.convo_prospects = []
        self.selected_convo_pid = None

        self._build_ui()
        self.model_var.trace_add("write", self._on_model_changed)
        self._load_models()
        self.refresh_review()
        self.refresh_outreach()
        self.refresh_conversations()

    def _on_model_changed(self, *_):
        self.model = self.model_var.get()

    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background="#1a1a2e", borderwidth=0)
        style.configure("TNotebook.Tab", background="#16213e", foreground="#eee",
                        padding=(14, 6))
        style.map("TNotebook.Tab", background=[("selected", "#0f3460")],
                  foreground=[("selected", "#4ecca3")])

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.chat_tab = ttk.Frame(self.notebook)
        self.review_tab = ttk.Frame(self.notebook)
        self.outreach_tab = ttk.Frame(self.notebook)
        self.convos_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.chat_tab, text="Chat")
        self.notebook.add(self.review_tab, text="Prospect Review")
        self.notebook.add(self.outreach_tab, text="Outreach Review")
        self.notebook.add(self.convos_tab, text="Conversations")

        self._build_chat_ui(self.chat_tab)
        self._build_review_ui(self.review_tab)
        self._build_outreach_ui(self.outreach_tab)
        self._build_conversations_ui(self.convos_tab)

    # ---------------- Chat tab (existing behaviour) ----------------
    def _build_chat_ui(self, parent):
        top = tk.Frame(parent, bg="#1a1a2e")
        top.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(top, text="Web Map Agent", bg="#1a1a2e", fg="#e94560",
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)

        model_frame = tk.Frame(top, bg="#1a1a2e")
        model_frame.pack(side=tk.RIGHT)
        tk.Label(model_frame, text="Model:", bg="#1a1a2e", fg="#eee",
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 5))

        self.model_var = tk.StringVar(value=self.model)
        self.model_menu = tk.OptionMenu(model_frame, self.model_var, self.model)
        self.model_menu.config(bg="#16213e", fg="#eee", font=("Segoe UI", 9),
                               activebackground="#0f3460", highlightthickness=0)
        self.model_menu["menu"].config(bg="#16213e", fg="#eee")
        self.model_menu.pack(side=tk.LEFT)

        self.status = tk.Label(top, bg="#1a1a2e", fg="#4ecca3",
                               font=("Segoe UI", 9), text="Ready")
        self.status.pack(side=tk.RIGHT, padx=10)

        self.chat = scrolledtext.ScrolledText(
            parent, wrap=tk.WORD, bg="#16213e", fg="#eee",
            font=("Consolas", 10), bd=0, highlightthickness=0,
            insertbackground="#eee", state=tk.DISABLED, padx=10, pady=10
        )
        self.chat.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.chat.tag_config("user", foreground="#e94560")
        self.chat.tag_config("agent", foreground="#4ecca3")
        self.chat.tag_config("tool", foreground="#f5a623")
        self.chat.tag_config("error", foreground="#ff6b6b")
        self.chat.tag_config("sys", foreground="#666")

        bottom = tk.Frame(parent, bg="#1a1a2e")
        bottom.pack(fill=tk.X, padx=10, pady=(5, 10))

        self.input_box = tk.Text(
            bottom, height=2, bg="#0f3460", fg="#eee",
            font=("Segoe UI", 11), bd=0, highlightthickness=1,
            highlightbackground="#e94560", highlightcolor="#e94560",
            insertbackground="#eee", wrap=tk.WORD, padx=8, pady=6
        )
        self.input_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.input_box.bind("<Return>", self._on_enter)
        self.input_box.bind("<Shift-Return>", lambda e: None)

        btn_frame = tk.Frame(bottom, bg="#1a1a2e")
        btn_frame.pack(side=tk.RIGHT)

        self.send_btn = tk.Button(btn_frame, text="Send", bg="#e94560", fg="#fff",
                                  font=("Segoe UI", 10, "bold"), bd=0, padx=15, pady=6,
                                  command=self._send, activebackground="#c73e54")
        self.send_btn.pack(side=tk.TOP, pady=(0, 4))

        tk.Button(btn_frame, text="Clear", bg="#666", fg="#fff",
                  font=("Segoe UI", 9), bd=0, padx=10, pady=4,
                  command=self._clear, activebackground="#555").pack(side=tk.TOP)

    # ---------------- Prospect Review tab ----------------
    def _build_review_ui(self, parent):
        toolbar = tk.Frame(parent, bg="#1a1a2e")
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(toolbar, text="Prospect Review", bg="#1a1a2e", fg="#4ecca3",
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)

        self.filter_var = tk.StringVar(value="All")
        self.filter_menu = tk.OptionMenu(toolbar, self.filter_var, *STATUS_FILTERS.keys(),
                                         command=lambda _: self.refresh_review())
        self.filter_menu.config(bg="#16213e", fg="#eee", font=("Segoe UI", 9),
                                activebackground="#0f3460", highlightthickness=0)
        self.filter_menu["menu"].config(bg="#16213e", fg="#eee")
        self.filter_menu.pack(side=tk.LEFT, padx=8)

        self.review_count = tk.Label(toolbar, bg="#1a1a2e", fg="#999",
                                     font=("Segoe UI", 9), text="")
        self.review_count.pack(side=tk.LEFT, padx=8)

        tk.Button(toolbar, text="Refresh", bg="#0f3460", fg="#eee",
                  font=("Segoe UI", 9), bd=0, padx=12, pady=3,
                  command=self.refresh_review, activebackground="#16213e"
                  ).pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left = tk.Frame(paned, bg="#16213e")
        right = tk.Frame(paned, bg="#16213e")
        paned.add(left, weight=2)
        paned.add(right, weight=3)

        self.review_list = tk.Listbox(left, bg="#16213e", fg="#eee",
                                      font=("Consolas", 10), selectbackground="#0f3460",
                                      selectforeground="#4ecca3", bd=0, highlightthickness=0)
        sb = tk.Scrollbar(left, command=self.review_list.yview)
        self.review_list.config(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.review_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.review_list.bind("<<ListboxSelect>>", self._on_select)

        self.detail = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, bg="#0f3460", fg="#eee", font=("Consolas", 10),
            bd=0, highlightthickness=0, state=tk.DISABLED, padx=10, pady=10)
        self.detail.pack(fill=tk.BOTH, expand=True)
        self.detail.tag_config("h", foreground="#4ecca3", font=("Segoe UI", 11, "bold"))
        self.detail.tag_config("warn", foreground="#f5a623")
        self.detail.tag_config("ok", foreground="#4ecca3")
        self.detail.tag_config("err", foreground="#e94560")

        actions = tk.Frame(parent, bg="#1a1a2e")
        actions.pack(fill=tk.X, padx=10, pady=(0, 10))

        tk.Label(actions, text="Reason (calibration):", bg="#1a1a2e", fg="#999",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        self.reason_var = tk.StringVar()
        self.reason_entry = tk.Entry(actions, textvariable=self.reason_var,
                                     bg="#0f3460", fg="#eee", font=("Segoe UI", 9),
                                     insertbackground="#eee", width=34)
        self.reason_entry.pack(side=tk.LEFT, padx=(0, 10), fill=tk.X, expand=True)

        tk.Button(actions, text="Research More", bg="#0f3460", fg="#eee",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=12, pady=5,
                  command=lambda: self._review_action("research"),
                  activebackground="#16213e").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(actions, text="Approve", bg="#4ecca3", fg="#0f3460",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=16, pady=5,
                  command=lambda: self._review_action("ready_for_outreach"),
                  activebackground="#3bb78f").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(actions, text="Reject", bg="#e94560", fg="#fff",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=16, pady=5,
                  command=lambda: self._review_action("do_not_contact"),
                  activebackground="#c73e54").pack(side=tk.LEFT)

    # ---------------- Review logic ----------------
    def refresh_review(self):
        self.review_prospects = []
        self.selected_pid = None
        status = STATUS_FILTERS.get(self.filter_var.get()) if hasattr(self, "filter_var") else None
        try:
            store = Store(DB_PATH)
            try:
                prospects = store.prospects(status=status)
            finally:
                store.close()
        except Exception:
            prospects = []
        self.review_prospects = prospects
        self.review_list.delete(0, tk.END)
        for p in prospects:
            score = p.get("total_score", 0)
            label = f"{score:5.1f}  {p.get('full_name','')[:26]:<26} {p.get('role_category','')[:16]:<16} {p.get('status','')}"
            self.review_list.insert(tk.END, label)
        self.review_count.config(text=f"{len(prospects)} prospects")
        self._show_detail(None)

    def _on_select(self, _event):
        sel = self.review_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.review_prospects):
            self.selected_pid = self.review_prospects[idx]["prospect_id"]
            self._show_detail(self.review_prospects[idx])

    def _show_detail(self, p):
        self.detail.config(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        if not p:
            self.detail.insert(tk.END, "Select a prospect to review.", "h")
            self.detail.config(state=tk.DISABLED)
            return
        d = self.detail
        d.insert(tk.END, f"{p.get('full_name','')}\n", "h")
        d.insert(tk.END, f"{p.get('raw_position') or 'unknown role'} @ {p.get('current_company') or 'unknown'}\n\n")

        d.insert(tk.END, "Score dimensions\n", "h")
        d.insert(tk.END, f"  Role fit:       {p.get('role_fit',0):.0f}/100\n")
        d.insert(tk.END, f"  Company fit:    {p.get('company_fit',0):.0f}/100\n")
        d.insert(tk.END, f"  Relationship:   {p.get('relationship_score',0):.0f}/100\n")
        d.insert(tk.END, f"  Engagement:     {p.get('engagement_score',0):.0f}/100\n")
        pf = p.get('problem_fit_score')
        d.insert(tk.END, f"  Problem fit:    {f'{pf:.0f}/100' if pf is not None else 'unknown'}\n")
        d.insert(tk.END, f"  Buying signal:  {p.get('buying_signal_score',0):.0f}/100"
                         f" ({p.get('commercial_intent','unknown')})\n")
        d.insert(tk.END, f"  Relevance:      {p.get('relevance_score',0):.0f}/100\n")
        d.insert(tk.END, f"  Overall:        {p.get('total_score',0):.0f}/100\n")

        d.insert(tk.END, "\nQualification\n", "h")
        d.insert(tk.END, f"  Fit:            {p.get('qualification_fit','-')}/100\n")
        conf = p.get('qualification_confidence')
        d.insert(tk.END, f"  Confidence:     {f'{conf:.2f}' if conf is not None else '-'}\n")
        d.insert(tk.END, f"  Research mode:  {p.get('research_mode') or 'none'}\n")
        d.insert(tk.END, f"  Next action:    {p.get('next_action') or '-'}\n")
        d.insert(tk.END, f"  Status:         {p.get('status','')}\n")
        d.insert(tk.END, f"  Pain state:     {p.get('pain_state') or 'not_researched'}\n")
        d.insert(tk.END, f"  Segmentation:   {p.get('segmentation_status') or 'unsegmented'}\n")

        segs = p.get('segments') or []
        if segs:
            d.insert(tk.END, "\nSegments\n", "h")
            d.insert(tk.END, "  " + ", ".join(segs) + "\n")
        missing = p.get('missing_signals') or []
        if missing:
            d.insert(tk.END, "  Missing signals:\n", "warn")
            for m in missing:
                d.insert(tk.END, f"    - {m}\n")

        reasons = p.get('selection_reasons') or []
        if reasons:
            d.insert(tk.END, "\nWHY_SELECTED\n", "h")
            for r in reasons:
                d.insert(tk.END, f"  {r}\n")

        d.insert(tk.END, "\nEvidence\n", "h")
        for e in p.get("evidence", []):
            d.insert(tk.END, f"  • {e}\n")
        convs = p.get("conversation_history", [])
        if convs:
            d.insert(tk.END, "\nConversations\n", "h")
            for c in convs[:5]:
                d.insert(tk.END, f"  • {c}\n")
        ci = p.get("conversation_intelligence")
        if ci:
            d.insert(tk.END, "\nConversation intelligence\n", "h")
            for k in ("relationship_state", "sentiment", "product_interest",
                      "pain_signal", "commercial_intent", "confidence"):
                d.insert(tk.END, f"  {k}: {ci.get(k, 'unknown')}\n")
        if p.get("linkedin_url"):
            d.insert(tk.END, f"\nURL: {p['linkedin_url']}\n")
        d.config(state=tk.DISABLED)

    def _review_action(self, new_status):
        if not self.selected_pid:
            return
        next_action = {
            "ready_for_outreach": "READY_FOR_OUTREACH",
            "do_not_contact": "DO_NOT_CONTACT",
            "research": "RESEARCH_MORE",
        }[new_status]
        reason = self.reason_var.get().strip()
        try:
            store = Store(DB_PATH)
            try:
                before = None
                for p in self.review_prospects:
                    if p["prospect_id"] == self.selected_pid:
                        before = p.get("status")
                        break
                store.record_feedback(self.selected_pid, new_status,
                                      reason=reason, status_before=before)
                store.set_status(self.selected_pid, new_status, next_action)
            finally:
                store.close()
        except Exception as e:
            self._append(f"Review error: {e}", "error")
            return
        self.reason_var.set("")
        self.refresh_review()

    # ---------------- Outreach Review tab ----------------
    def _build_outreach_ui(self, parent):
        toolbar = tk.Frame(parent, bg="#1a1a2e")
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(toolbar, text="Outreach Review", bg="#1a1a2e", fg="#4ecca3",
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)

        self.outreach_campaign_var = tk.StringVar(value="All")
        self.outreach_campaign_menu = tk.OptionMenu(
            toolbar, self.outreach_campaign_var, "All",
            command=lambda _: self.refresh_outreach())
        self.outreach_campaign_menu.config(bg="#16213e", fg="#eee",
                                           font=("Segoe UI", 9),
                                           activebackground="#0f3460",
                                           highlightthickness=0)
        self.outreach_campaign_menu["menu"].config(bg="#16213e", fg="#eee")
        self.outreach_campaign_menu.pack(side=tk.LEFT, padx=8)

        self.outreach_count = tk.Label(toolbar, bg="#1a1a2e", fg="#999",
                                       font=("Segoe UI", 9), text="")
        self.outreach_count.pack(side=tk.LEFT, padx=8)

        tk.Button(toolbar, text="Refresh", bg="#0f3460", fg="#eee",
                  font=("Segoe UI", 9), bd=0, padx=12, pady=3,
                  command=self.refresh_outreach, activebackground="#16213e"
                  ).pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left = tk.Frame(paned, bg="#16213e")
        right = tk.Frame(paned, bg="#16213e")
        paned.add(left, weight=2)
        paned.add(right, weight=3)

        self.outreach_list = tk.Listbox(left, bg="#16213e", fg="#eee",
                                        font=("Consolas", 10),
                                        selectbackground="#0f3460",
                                        selectforeground="#4ecca3", bd=0,
                                        highlightthickness=0)
        sb = tk.Scrollbar(left, command=self.outreach_list.yview)
        self.outreach_list.config(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.outreach_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.outreach_list.bind("<<ListboxSelect>>", self._on_outreach_select)

        self.outreach_detail = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, bg="#0f3460", fg="#eee", font=("Consolas", 10),
            bd=0, highlightthickness=0, state=tk.DISABLED, padx=10, pady=10)
        self.outreach_detail.pack(fill=tk.BOTH, expand=True)
        self.outreach_detail.tag_config("h", foreground="#4ecca3",
                                        font=("Segoe UI", 11, "bold"))
        self.outreach_detail.tag_config("warn", foreground="#f5a623")
        self.outreach_detail.tag_config("ok", foreground="#4ecca3")
        self.outreach_detail.tag_config("err", foreground="#e94560")

        actions = tk.Frame(parent, bg="#1a1a2e")
        actions.pack(fill=tk.X, padx=10, pady=(0, 10))

        tk.Button(actions, text="Approve", bg="#4ecca3", fg="#0f3460",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=16, pady=5,
                  command=lambda: self._review_action("ready_for_outreach"),
                  activebackground="#3bb78f").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(actions, text="Approve All", bg="#2ecc71", fg="#0f3460",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=16, pady=5,
                  command=self._approve_all,
                  activebackground="#27ae60").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(actions, text="Reject", bg="#e94560", fg="#fff",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=16, pady=5,
                  command=lambda: self._outreach_action("reject"),
                  activebackground="#c73e54").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(actions, text="Regenerate", bg="#0f3460", fg="#eee",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=12, pady=5,
                  command=lambda: self._outreach_action("regenerate"),
                  activebackground="#16213e").pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(actions, text="Edit", bg="#666", fg="#fff",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=12, pady=5,
                  command=lambda: self._outreach_action("edit"),
                  activebackground="#555").pack(side=tk.LEFT)

    def _load_campaign_choices(self):
        try:
            store = Store(DB_PATH)
            try:
                names = [c["campaign_id"] for c in store.campaigns()]
            finally:
                store.close()
        except Exception:
            names = []
        if not names:
            try:
                store = Store(DB_PATH)
                try:
                    sync_campaigns(store)
                    names = [c["campaign_id"] for c in store.campaigns()]
                finally:
                    store.close()
            except Exception:
                names = []
        menu = self.outreach_campaign_menu["menu"]
        menu.delete(0, "end")
        for n in ["All"] + names:
            menu.add_command(label=n, command=lambda v=n: self.outreach_campaign_var.set(v))
        if self.outreach_campaign_var.get() not in ["All"] + names:
            self.outreach_campaign_var.set("All")

    def refresh_outreach(self):
        self.outreach_messages = []
        self.selected_msg_id = None
        self._load_campaign_choices()
        campaign_id = None if self.outreach_campaign_var.get() == "All" \
            else self.outreach_campaign_var.get()
        try:
            store = Store(DB_PATH)
            try:
                msgs = approval_queue(store, campaign_id)
            finally:
                store.close()
        except Exception:
            msgs = []
        self.outreach_messages = msgs
        self.outreach_list.delete(0, tk.END)
        for m in msgs:
            try:
                store = Store(DB_PATH)
                try:
                    p = store.get_prospect(m["prospect_id"])
                    cp = store.get_campaign_prospect(m["campaign_id"], m["prospect_id"])
                finally:
                    store.close()
            except Exception:
                p, cp = None, None
            name = (p or {}).get("full_name", m["prospect_id"])
            prio = (cp or {}).get("priority", 0)
            label = (f"{prio:5.1f}  {m.get('strategy','')[:18]:<18} "
                     f"{name[:22]:<22} {m.get('version','')} {m.get('status','')}")
            self.outreach_list.insert(tk.END, label)
        self.outreach_count.config(text=f"{len(msgs)} messages in queue")
        self._show_outreach_detail(None)

    def _on_outreach_select(self, _event):
        sel = self.outreach_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.outreach_messages):
            self.selected_msg_id = self.outreach_messages[idx]["message_id"]
            self._show_outreach_detail(self.outreach_messages[idx])

    def _show_outreach_detail(self, m):
        self.outreach_detail.config(state=tk.NORMAL)
        self.outreach_detail.delete("1.0", tk.END)
        if not m:
            self.outreach_detail.insert(tk.END, "Select a message to review.", "h")
            self.outreach_detail.config(state=tk.DISABLED)
            return
        d = self.outreach_detail
        try:
            store = Store(DB_PATH)
            try:
                p = store.get_prospect(m["prospect_id"])
                cp = store.get_campaign_prospect(m["campaign_id"], m["prospect_id"])
            finally:
                store.close()
        except Exception:
            p, cp = None, None

        d.insert(tk.END, "MESSAGE REVIEW\n", "h")
        d.insert(tk.END, f"{m['message_id']}  {m.get('version')}  "
                         f"strategy={m.get('strategy')}  "
                         f"confidence={m.get('confidence')}\n")
        d.insert(tk.END, f"status: {m.get('status')}\n\n")

        d.insert(tk.END, "PROSPECT\n", "h")
        d.insert(tk.END, f"  {p.get('full_name','?') if p else '?'} - "
                         f"{p.get('raw_position','?') if p else '?'} @ "
                         f"{p.get('current_company','?') if p else '?'}\n")
        d.insert(tk.END, f"  ICP fit:        {p.get('qualification_fit','-') if p else '-'}/100\n")
        pf = (p or {}).get('problem_fit_score')
        d.insert(tk.END, f"  Product fit:    {f'{pf:.0f}/100' if pf is not None else 'unknown'}\n")
        d.insert(tk.END, f"  Evidence conf:  {p.get('qualification_confidence','-') if p else '-'}\n")
        d.insert(tk.END, f"  Segment:        {', '.join((p or {}).get('segments') or ['unsegmented'])}\n")
        d.insert(tk.END, f"  Pain state:     {(p or {}).get('pain_state') or 'not_researched'}\n")
        d.insert(tk.END, f"  Outreach state: {cp.get('status') if cp else '-'}\n\n")

        d.insert(tk.END, "WHY\n", "h")
        for v in m.get("validation") or []:
            tag = "ok" if v.startswith("ok") else ("err" if v.startswith("fail") else "warn")
            d.insert(tk.END, f"  [{tag}] {v}\n", tag)
        for e in m.get("evidence_used") or []:
            d.insert(tk.END, f"  used: {e}\n")

        d.insert(tk.END, "\nMESSAGE\n", "h")
        d.insert(tk.END, f"{m.get('text','')}\n")
        for c in m.get("claims") or []:
            d.insert(tk.END, f"  claim: {c}\n", "warn")
        d.config(state=tk.DISABLED)

    def _outreach_action(self, action):
        if not self.selected_msg_id:
            return
        mid = self.selected_msg_id
        store = Store(DB_PATH)
        try:
            m = store.get_message(mid)
            if not m:
                return
            if action == "approve":
                approve(store, mid)
            elif action == "reject":
                reject(store, mid, note="rejected in Outreach Review")
            elif action == "regenerate":
                campaign = store.get_campaign(m["campaign_id"])
                cp = store.get_campaign_prospect(m["campaign_id"], m["prospect_id"])
                if campaign and cp:
                    produce_message(store, campaign, cp, model=self.model)
            elif action == "edit":
                self._edit_message_dialog(m)
        finally:
            store.close()
        self.refresh_outreach()

    def _approve_all(self):
        try:
            store = Store(DB_PATH)
            try:
                prospects = store.prospects(status="ready_for_human_review")
                if not prospects:
                    self._append("No prospects ready for review.", "sys")
                    return
                count = 0
                for p in prospects:
                    store.record_feedback(p["prospect_id"], "approved",
                                          reason="bulk approve from GUI",
                                          status_before="ready_for_human_review")
                    store.set_status(p["prospect_id"], "ready_for_outreach",
                                     next_action="READY_FOR_OUTREACH")
                    count += 1
                self._append(f"Approved {count} prospects to ready_for_outreach", "sys")
            finally:
                store.close()
        except Exception as e:
            self._append(f"Approve all error: {e}", "error")
        self.refresh_outreach()

    def _edit_message_dialog(self, m):
        top = tk.Toplevel(self.root)
        top.title(f"Edit message {m['message_id']} (creates a new version)")
        top.geometry("640x360")
        top.configure(bg="#16213e")
        top.transient(self.root)
        box = scrolledtext.ScrolledText(top, wrap=tk.WORD, bg="#0f3460", fg="#eee",
                                        font=("Consolas", 10), insertbackground="#eee")
        box.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        box.insert("1.0", m.get("text", ""))
        note = tk.Label(top, text="A new immutable version is created; the old "
                                  "one is kept. It still needs your Approve.",
                        bg="#16213e", fg="#999", font=("Segoe UI", 9))
        note.pack(fill=tk.X, padx=10)

        def _save():
            text = box.get("1.0", "end-1c").strip()
            if not text:
                return
            store = Store(DB_PATH)
            try:
                import uuid
                import re
                from datetime import datetime, timezone
                existing = store.messages_for(m["prospect_id"], m["campaign_id"])
                nums = []
                for x in existing:
                    mm = re.search(r"(\d+)$", str(x.get("version")))
                    if mm:
                        nums.append(int(mm.group(1)))
                new = {
                    "message_id": "msg_" + uuid.uuid4().hex[:10],
                    "prospect_id": m["prospect_id"],
                    "campaign_id": m["campaign_id"],
                    "strategy": m.get("strategy"),
                    "version": f"v{(max(nums) + 1) if nums else 1}",
                    "text": text,
                    "claims": m.get("claims", []),
                    "evidence_used": m.get("evidence_used", []),
                    "confidence": m.get("confidence", 0.0),
                    "approved": False,
                    "validation": ["ok: human edit", "flag: human-edited text"],
                    "status": "approved",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                store.save_message(new)
            finally:
                store.close()
            top.destroy()
            self.refresh_outreach()

        btn = tk.Button(top, text="Save new version", bg="#4ecca3", fg="#0f3460",
                        font=("Segoe UI", 10, "bold"), bd=0, padx=14, pady=5,
                        command=_save, activebackground="#3bb78f")
        btn.pack(pady=(0, 10))

    # ---------------- Conversations tab ----------------
    def _build_conversations_ui(self, parent):
        toolbar = tk.Frame(parent, bg="#1a1a2e")
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(toolbar, text="Conversations", bg="#1a1a2e", fg="#4ecca3",
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)

        self.convo_count = tk.Label(toolbar, bg="#1a1a2e", fg="#999",
                                    font=("Segoe UI", 9), text="")
        self.convo_count.pack(side=tk.LEFT, padx=8)

        tk.Button(toolbar, text="Refresh", bg="#0f3460", fg="#eee",
                  font=("Segoe UI", 9), bd=0, padx=12, pady=3,
                  command=self.refresh_conversations, activebackground="#16213e"
                  ).pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left = tk.Frame(paned, bg="#16213e")
        right = tk.Frame(paned, bg="#16213e")
        paned.add(left, weight=2)
        paned.add(right, weight=3)

        self.convo_list = tk.Listbox(left, bg="#16213e", fg="#eee",
                                     font=("Consolas", 10),
                                     selectbackground="#0f3460",
                                     selectforeground="#4ecca3", bd=0,
                                     highlightthickness=0)
        sb = tk.Scrollbar(left, command=self.convo_list.yview)
        self.convo_list.config(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.convo_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.convo_list.bind("<<ListboxSelect>>", self._on_convo_select)

        self.convo_detail = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, bg="#0f3460", fg="#eee", font=("Consolas", 10),
            bd=0, highlightthickness=0, state=tk.DISABLED, padx=10, pady=10)
        self.convo_detail.pack(fill=tk.BOTH, expand=True)
        self.convo_detail.tag_config("h", foreground="#4ecca3",
                                     font=("Segoe UI", 11, "bold"))
        self.convo_detail.tag_config("warn", foreground="#f5a623")
        self.convo_detail.tag_config("ok", foreground="#4ecca3")
        self.convo_detail.tag_config("err", foreground="#e94560")

        bottom = tk.Frame(parent, bg="#1a1a2e")
        bottom.pack(fill=tk.X, padx=10, pady=(5, 10))

        tk.Label(bottom, text="Paste inbound reply:", bg="#1a1a2e", fg="#999",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        self.reply_var = tk.StringVar()
        self.reply_entry = tk.Entry(bottom, textvariable=self.reply_var,
                                    bg="#0f3460", fg="#eee", font=("Segoe UI", 9),
                                    insertbackground="#eee", width=40)
        self.reply_entry.pack(side=tk.LEFT, padx=(0, 8), fill=tk.X, expand=True)

        tk.Button(bottom, text="Classify Reply", bg="#4ecca3", fg="#0f3460",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=12, pady=5,
                  command=self._classify_reply, activebackground="#3bb78f"
                  ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(bottom, text="Mark Sent", bg="#0f3460", fg="#eee",
                  font=("Segoe UI", 9, "bold"), bd=0, padx=12, pady=5,
                  command=self._mark_sent, activebackground="#16213e"
                  ).pack(side=tk.LEFT)

    def refresh_conversations(self):
        self.convo_prospects = []
        self.selected_convo_pid = None
        try:
            store = Store(DB_PATH)
            try:
                cps = store.campaign_prospects()
            finally:
                store.close()
        except Exception:
            cps = []
        rows = []
        for cp in cps:
            if cp.get("status") in ("RESPONDED", "CONVERSATION", "INTERESTED",
                                    "HIGH_INTENT", "NOT_INTERESTED",
                                    "LINK_SHARED", "VISITED", "SIGNUP_STARTED",
                                    "SIGNED_UP", "ACTIVATED", "CUSTOMER",
                                    "AWAITING_RESPONSE"):
                rows.append(cp)
        self.convo_prospects = rows
        self.convo_list.delete(0, tk.END)
        for cp in rows:
            try:
                store = Store(DB_PATH)
                try:
                    p = store.get_prospect(cp["prospect_id"])
                finally:
                    store.close()
            except Exception:
                p = None
            name = (p or {}).get("full_name", cp["prospect_id"])
            self.convo_list.insert(tk.END,
                                   f"{name[:26]:<26} {cp.get('campaign_id','')[:14]:<14} {cp.get('status','')}")
        self.convo_count.config(text=f"{len(rows)} conversations")
        self._show_convo_detail(None)

    def _on_convo_select(self, _event):
        sel = self.convo_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.convo_prospects):
            self.selected_convo_pid = self.convo_prospects[idx]["prospect_id"]
            self._show_convo_detail(self.convo_prospects[idx])

    def _show_convo_detail(self, cp):
        self.convo_detail.config(state=tk.NORMAL)
        self.convo_detail.delete("1.0", tk.END)
        if not cp:
            self.convo_detail.insert(tk.END, "Select a conversation.", "h")
            self.convo_detail.config(state=tk.DISABLED)
            return
        d = self.convo_detail
        store = Store(DB_PATH)
        try:
            p = store.get_prospect(cp["prospect_id"])
            convs = store.conversations_outreach(cp["prospect_id"])
            events = store.outreach_events(cp["prospect_id"], cp["campaign_id"])
            msgs = store.messages_for(cp["prospect_id"], cp["campaign_id"])
        finally:
            store.close()

        d.insert(tk.END, f"{(p or {}).get('full_name', '?')}\n", "h")
        d.insert(tk.END, f"campaign={cp.get('campaign_id')}  state={cp.get('status')}\n\n")

        d.insert(tk.END, "MESSAGES SENT\n", "h")
        for m in msgs:
            d.insert(tk.END, f"  {m.get('version','')} [{m.get('status','')}] "
                             f"v:{m.get('strategy','')} - {m.get('text','')[:120]}\n")

        d.insert(tk.END, "\nREPLY CLASSIFICATIONS\n", "h")
        for c in convs:
            tag = "ok" if c.get("intent") in ("interest", "question") else "warn"
            d.insert(tk.END, f"  intent={c.get('intent')} sentiment={c.get('sentiment')} "
                             f"pain={c.get('pain_signal')} commercial={c.get('commercial_intent')} "
                             f"objection={c.get('objection')} conf={c.get('confidence')}\n", tag)

        d.insert(tk.END, "\nSTATE HISTORY\n", "h")
        for e in events:
            d.insert(tk.END, f"  {e.get('event')}: {e.get('from_state')} -> "
                             f"{e.get('to_state')}\n")
        d.config(state=tk.DISABLED)

    def _classify_reply(self):
        if not self.selected_convo_pid:
            return
        reply = self.reply_var.get().strip()
        if not reply:
            return
        cp = next((c for c in self.convo_prospects
                   if c["prospect_id"] == self.selected_convo_pid), None)
        if not cp:
            return
        try:
            store = Store(DB_PATH)
            try:
                p = store.get_prospect(self.selected_convo_pid)
                campaign = store.get_campaign(cp["campaign_id"])
                result = register_reply(store, campaign, self.selected_convo_pid,
                                        reply, model=self.model)
                note = (f"intent={result['classification'].get('intent')} "
                        f"objection={result['classification'].get('objection')}")
                if result.get("objection_draft"):
                    note += " (objection draft queued for approval)"
            finally:
                store.close()
        except Exception as e:
            self._append(f"Classify error: {e}", "error")
            return
        self.reply_var.set("")
        self.refresh_conversations()
        self._append(f"Reply classified: {note}", "sys")

    def _mark_sent(self):
        if not self.selected_convo_pid:
            return
        cp = next((c for c in self.convo_prospects
                   if c["prospect_id"] == self.selected_convo_pid), None)
        if not cp:
            return
        try:
            store = Store(DB_PATH)
            try:
                mark_sent(store, cp)
            finally:
                store.close()
        except Exception as e:
            self._append(f"Mark sent error: {e}", "error")
            return
        self.refresh_conversations()

    # ---------------- Chat helpers (existing behaviour) ----------------
    def _append(self, text, tag="agent"):
        self.chat.config(state=tk.NORMAL)
        self.chat.insert(tk.END, text + "\n", tag)
        self.chat.see(tk.END)
        self.chat.config(state=tk.DISABLED)

    def _on_enter(self, event):
        if not event.state & 0x1:
            self._send()
            return "break"

    def _load_models(self):
        def _load():
            try:
                resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
                models = [m["name"] for m in resp.json().get("models", [])]
                self.root.after(0, lambda: self._update_models(models))
            except Exception:
                pass
        threading.Thread(target=_load, daemon=True).start()

    def _update_models(self, models):
        all_models = list(models)
        for extra in ("gemma4:31b-cloud", "nemotron-3-ultra:cloud"):
            if extra not in all_models:
                all_models.append(extra)
        self.model_menu["menu"].delete(0, "end")
        for m in all_models:
            self.model_menu["menu"].add_command(label=m, command=lambda v=m: self.model_var.set(v))
        if self.model not in all_models:
            self.model = all_models[0]
        self.model_var.set(self.model)

    def _send(self):
        msg = self.input_box.get("1.0", tk.END).strip()
        if not msg or self.running:
            return
        self.input_box.delete("1.0", tk.END)
        self._append(f"You: {msg}", "user")
        self.messages.append({"role": "user", "content": msg})
        self.running = True
        self.send_btn.config(state=tk.DISABLED)
        self.status.config(text="Thinking...")
        threading.Thread(target=self._agent_loop, daemon=True).start()

    def _agent_loop(self):
        from tools import TOOLS, CUSTOM_TOOLS, delete_tool

        for step in range(25):
            self.root.after(0, lambda s=step: self.status.config(text=f"Step {s+1}..."))

            schemas = []
            for name, tool in TOOLS.items():
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                })

            payload = {
                "model": self.model_var.get(),
                "messages": self.messages,
                "tools": schemas,
                "stream": False,
                "options": {"num_ctx": 16384, "temperature": 0.3},
            }

            try:
                start = time.time()
                resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=600)
                elapsed = time.time() - start

                if resp.status_code != 200:
                    self.root.after(0, lambda: self._append(f"Error: Ollama {resp.status_code}", "error"))
                    break

                data = resp.json()
                message = data.get("message", {})
                content = message.get("content", "")
                tool_calls = message.get("tool_calls", [])

                self.root.after(0, lambda e=elapsed: self._append(f"({e:.1f}s)", "sys"))

                if content:
                    self.root.after(0, lambda c=content: self._append(f"Agent: {c}", "agent"))

                if not tool_calls:
                    self.messages.append({"role": "assistant", "content": content})
                    break

                self.messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args_raw = func.get("arguments", "{}")
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw

                    self.root.after(0, lambda n=name, a=args: self._append(
                        f"  -> {n}({json.dumps(a, ensure_ascii=False)[:200]})", "tool"
                    ))

                    if name in TOOLS:
                        try:
                            result = TOOLS[name]["function"](**args)
                            if name in CUSTOM_TOOLS:
                                delete_tool(name)
                        except Exception as e:
                            if name in CUSTOM_TOOLS:
                                delete_tool(name)
                            result = f"Error: {e}"
                    else:
                        result = f"Error: unknown tool '{name}'"

                    if len(result) > 1000000:
                        result = result[:1000000] + "..."

                    self.root.after(0, lambda n=name, r=result: self._append(
                        f"  <- {r}{'...' if len(r) > 1000000 else ''}", "tool"
                    ))

                    self.messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})

            except Exception as e:
                self.root.after(0, lambda err=str(e): self._append(f"Error: {err}", "error"))
                break

        self.root.after(0, self._done)

    def _done(self):
        self.running = False
        self.send_btn.config(state=tk.NORMAL)
        self.status.config(text="Ready")

    def _clear(self):
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT.format(cwd=os.getcwd())}]
        self.chat.config(state=tk.NORMAL)
        self.chat.delete("1.0", tk.END)
        self.chat.config(state=tk.DISABLED)
        from tools import cleanup_tools
        cleanup_tools()
        self._append("[Chat cleared]", "sys")


def main():
    root = tk.Tk()
    AgentGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
