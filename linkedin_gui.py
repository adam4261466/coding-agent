"""Visual LinkedIn prospect and conversation CRM."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import linkedin_agent as core
from linkedin_browser import fetch_profile

COLORS = {
    "not_contacted": "#2d7ff9",
    "contacted": "#f2994a",
    "needs_reply": "#e74c3c",
    "active": "#27ae60",
    "eliminated": "#7f8c8d",
}
LABELS = {
    "all": "All",
    "not_contacted": "Not contacted",
    "contacted": "Waiting for reply",
    "needs_reply": "Needs your reply",
    "active": "Active conversations",
    "eliminated": "Elimination Zone",
}


class LinkedInApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LinkedIn Conversation CRM")
        self.geometry("1450x900")
        self.minsize(1150, 720)
        self.selected_id: int | None = None
        self.rows = []
        self.category = "all"
        self._build()
        self.refresh_all()

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        side = ttk.Frame(self, padding=12)
        side.grid(row=0, column=0, sticky="nsew")
        side.columnconfigure(0, weight=1)
        side.rowconfigure(8, weight=1)
        ttk.Label(side, text="LinkedIn CRM", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(side, text="One prospect at a time", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=(0, 10))

        self.category_buttons = {}
        for r, key in enumerate(LABELS, start=2):
            button = ttk.Button(side, text=LABELS[key], command=lambda k=key: self.set_category(k))
            button.grid(row=r, column=0, sticky="ew", pady=2)
            self.category_buttons[key] = button

        ttk.Separator(side).grid(row=8, column=0, sticky="ew", pady=12)
        ttk.Button(side, text="＋ Add prospect by URL", command=self.add_by_url).grid(row=9, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(side, text="↻ Refresh", command=self.refresh_all).grid(row=10, column=0, sticky="ew", pady=(0, 10))

        legend = ttk.LabelFrame(side, text="Stage colors", padding=8)
        legend.grid(row=11, column=0, sticky="ew")
        for r, (key, text) in enumerate([
            ("not_contacted", "Not contacted"), ("contacted", "Waiting for reply"),
            ("needs_reply", "Needs your reply"), ("active", "Active conversation"),
            ("eliminated", "Elimination Zone"),
        ]):
            tk.Label(legend, text=f"● {text}", fg=COLORS[key], anchor="w", font=("Segoe UI", 9, "bold")).grid(row=r, column=0, sticky="w")

        main = ttk.Frame(self, padding=(4, 12, 12, 12))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)
        main.rowconfigure(6, weight=1)

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self.search_var = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.search_var, font=("Segoe UI", 11))
        search.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        search.bind("<KeyRelease>", lambda _e: self.refresh_list())
        self.count_var = tk.StringVar(value="0 prospects")
        ttk.Label(top, textvariable=self.count_var).grid(row=0, column=1)

        self.title_var = tk.StringVar(value="Select a prospect")
        ttk.Label(main, textvariable=self.title_var, font=("Segoe UI", 17, "bold")).grid(row=1, column=0, sticky="w", pady=(12, 2))

        self.step_labels = {}
        stepbar = ttk.Frame(main)
        stepbar.grid(row=2, column=0, sticky="ew", pady=(5, 10))
        for c in range(5): stepbar.columnconfigure(c, weight=1)
        for c, (key, text) in enumerate([
            ("not_contacted", "1 · Not contacted"), ("contacted", "2 · Contacted"),
            ("needs_reply", "3 · Needs reply"), ("active", "4 · Active"), ("eliminated", "X · Eliminated"),
        ]):
            label = tk.Label(stepbar, text=text, padx=7, pady=7, bg="#e8e8e8", fg="#555", font=("Segoe UI", 9, "bold"))
            label.grid(row=0, column=c, sticky="ew", padx=2)
            self.step_labels[key] = label

        list_frame = ttk.Frame(main)
        list_frame.grid(row=3, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(list_frame, activestyle="dotbox", font=("Segoe UI", 10), exportselection=False)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.bind("<<ListboxSelect>>", self.select_prospect)

        details = ttk.LabelFrame(main, text="Selected prospect", padding=10)
        details.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        details.columnconfigure(0, weight=1)
        self.info_var = tk.StringVar(value="Select someone from the list")
        ttk.Label(details, textvariable=self.info_var, font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w")
        self.action_var = tk.StringVar()
        ttk.Label(details, textvariable=self.action_var, font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(4, 0))

        actions = ttk.Frame(main)
        actions.grid(row=5, column=0, sticky="ew", pady=8)
        self.open_btn = ttk.Button(actions, text="Open LinkedIn", command=self.open_profile, state="disabled"); self.open_btn.pack(side="left", padx=(0,5))
        self.refresh_profile_btn = ttk.Button(actions, text="Read profile", command=self.read_profile, state="disabled"); self.refresh_profile_btn.pack(side="left", padx=5)
        self.initial_btn = ttk.Button(actions, text="Generate initial", command=self.generate_initial, state="disabled"); self.initial_btn.pack(side="left", padx=5)
        self.reply_btn = ttk.Button(actions, text="Generate reply", command=self.generate_reply, state="disabled"); self.reply_btn.pack(side="left", padx=5)
        self.eliminate_btn = ttk.Button(actions, text="Eliminate", command=self.eliminate, state="disabled"); self.eliminate_btn.pack(side="left", padx=(18,5))
        self.restore_btn = ttk.Button(actions, text="Restore", command=self.restore, state="disabled"); self.restore_btn.pack(side="left", padx=5)

        conversation = ttk.LabelFrame(main, text="Real conversation", padding=8)
        conversation.grid(row=6, column=0, sticky="nsew")
        conversation.columnconfigure(0, weight=1); conversation.rowconfigure(0, weight=1)
        self.history = tk.Text(conversation, height=10, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.history.grid(row=0, column=0, sticky="nsew")

        composer = ttk.LabelFrame(main, text="Draft / exact message input", padding=8)
        composer.grid(row=7, column=0, sticky="ew", pady=(8,0))
        composer.columnconfigure(0, weight=1)
        self.editor = tk.Text(composer, height=7, wrap="word", font=("Segoe UI", 10))
        self.editor.grid(row=0, column=0, sticky="ew")
        bar = ttk.Frame(composer); bar.grid(row=1, column=0, sticky="ew", pady=(8,0))
        ttk.Button(bar, text="Save as sent", command=lambda:self.save_message("outbound")).pack(side="left")
        ttk.Button(bar, text="Save prospect reply", command=lambda:self.save_message("inbound")).pack(side="left", padx=8)
        ttk.Button(bar, text="Clear", command=self.clear_editor).pack(side="left")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status_var, relief="sunken", anchor="w").grid(row=8, column=0, sticky="ew", pady=(8,0))

    def set_category(self, category):
        self.category = category; self.refresh_list()

    def _state(self, row):
        if row["eliminated"]: return "eliminated", "Do not contact"
        messages = core.get_messages(int(row["id"]))
        if not messages: return "not_contacted", "No communication yet"
        if messages[-1]["direction"] == "inbound": return "needs_reply", "Your turn to reply"
        return ("active", "Waiting for prospect" if len(messages) > 1 else "Waiting for prospect") if any(m["direction"] == "inbound" for m in messages) else ("contacted", "Waiting for prospect")

    def _matches(self, stage, action):
        return self.category == "all" or {"not_contacted":stage=="not_contacted", "contacted":stage=="contacted", "needs_reply":stage=="needs_reply", "active":stage=="active", "eliminated":stage=="eliminated"}.get(self.category, True)

    def refresh_counts(self):
        rows = core.search_prospects(limit=100000); counts={k:0 for k in LABELS if k!="all"}
        for row in rows:
            stage,_=self._state(row); counts[stage]+=1
        self.category_buttons["all"].configure(text=f"All ({len(rows)})")
        for k in counts: self.category_buttons[k].configure(text=f"{LABELS[k]} ({counts[k]})")

    def refresh_list(self):
        query=self.search_var.get().strip(); rows=[]
        for row in core.search_prospects(query=query,limit=100000):
            stage,action=self._state(row)
            if self._matches(stage,action): rows.append(row)
        self.rows=rows; self.listbox.delete(0,"end")
        for i,row in enumerate(rows):
            stage,action=self._state(row); key=stage; badge={"not_contacted":"NEW","contacted":"WAIT","needs_reply":"REPLY","active":"ACTIVE","eliminated":"X"}[stage]
            rel=" · Not connected" if row["relationship"] != "connected" else ""
            company=f" — {row['company']}" if row['company'] else ""
            self.listbox.insert("end",f"[{badge}] {row['first_name']} {row['last_name']}{company}{rel}")
            self.listbox.itemconfig(i,foreground=COLORS[key])
        self.count_var.set(f"{len(rows)} shown")

    def refresh_all(self):
        self.refresh_counts(); self.refresh_list(); self.refresh_selection()

    def select_prospect(self,_event=None):
        sel=self.listbox.curselection()
        if sel: self.selected_id=int(self.rows[sel[0]]["id"]); self.refresh_selection()

    def refresh_selection(self):
        if self.selected_id is None:
            self._clear_selected_ui(); return
        row=core.get_prospect(self.selected_id)
        if row is None: self.selected_id=None; self._clear_selected_ui(); return
        stage,action=self._state(row)
        self.title_var.set(f"{row['first_name']} {row['last_name']}")
        relationship="Connected" if row["relationship"]=="connected" else "Not connected"
        source="CSV connection" if row["source"]=="connections_csv" else "Added from profile URL"
        self.info_var.set(f"{row['position'] or row['profile_headline'] or 'Unknown role'}  ·  {row['company'] or 'Unknown company'}  ·  {relationship}  ·  {source}\n{row['url']}")
        self.action_var.set(f"STEP: {LABELS[stage]}   |   {action}")
        for key,label in self.step_labels.items(): label.configure(bg=COLORS[key] if key==stage else "#e8e8e8",fg="white" if key==stage else "#555")
        for b in (self.open_btn,self.refresh_profile_btn,self.initial_btn,self.reply_btn,self.eliminate_btn,self.restore_btn): b.configure(state="normal")
        self.eliminate_btn.configure(state="disabled" if row["eliminated"] else "normal")
        self.restore_btn.configure(state="normal" if row["eliminated"] else "disabled")
        self.reply_btn.configure(state="normal" if any(m["direction"]=="inbound" for m in core.get_messages(self.selected_id)) and not row["eliminated"] else "disabled")
        self._show_history(); self.clear_editor(); self.status_var.set("Ready")

    def _clear_selected_ui(self):
        self.title_var.set("Select a prospect"); self.info_var.set("Select someone from the list"); self.action_var.set("")
        for b in (self.open_btn,self.refresh_profile_btn,self.initial_btn,self.reply_btn,self.eliminate_btn,self.restore_btn): b.configure(state="disabled")
        self.history.configure(state="normal"); self.history.delete("1.0","end"); self.history.insert("end","No prospect selected.\n"); self.history.configure(state="disabled"); self.clear_editor()
        for label in self.step_labels.values(): label.configure(bg="#e8e8e8",fg="#555")

    def _show_history(self):
        self.history.configure(state="normal"); self.history.delete("1.0","end")
        rows=core.get_messages(self.selected_id) if self.selected_id else []
        if not rows: self.history.insert("end","No real messages recorded yet.\n")
        for row in rows:
            who="YOU" if row["direction"]=="outbound" else "PROSPECT"
            self.history.insert("end",f"{who} · {row['created_at']}\n{row['content']}\n\n")
        self.history.configure(state="disabled")

    def _require(self):
        if self.selected_id is None: raise ValueError("Select a prospect first")
        return self.selected_id

    def open_profile(self):
        try: core.open_profile(core.get_prospect(self._require())["url"]); self.status_var.set("Profile opened in browser.")
        except Exception as exc: messagebox.showerror("Open LinkedIn",str(exc))

    def read_profile(self): self._run_profile_read(open_after=True)

    def add_by_url(self):
        url=simpledialog.askstring("Add prospect by LinkedIn URL","Paste the full LinkedIn profile URL:",parent=self)
        if not url: return
        self._status_busy("Reading LinkedIn profile…")
        def worker():
            try:
                profile=fetch_profile(url)
                pid=core.add_direct_profile(profile)
                self.after(0,lambda:self._direct_profile_done(pid))
            except Exception as exc: self.after(0,lambda:self._profile_error(exc))
        threading.Thread(target=worker,daemon=True).start()

    def _run_profile_read(self,open_after=False):
        try: pid=self._require(); row=core.get_prospect(pid)
        except Exception as exc: messagebox.showerror("Profile",str(exc)); return
        self._status_busy("Reading LinkedIn profile…")
        def worker():
            try:
                profile=fetch_profile(row["url"])
                pid=core.add_direct_profile(profile)
                if open_after: core.open_profile(row["url"])
                self.after(0,lambda:self._direct_profile_done(pid))
            except Exception as exc: self.after(0,lambda:self._profile_error(exc))
        threading.Thread(target=worker,daemon=True).start()

    def _direct_profile_done(self,pid):
        self.selected_id=pid; self.refresh_all(); self._select_current(); self.status_var.set("Profile captured. Generate initial to draft the message.")

    def _select_current(self):
        if self.selected_id is None: return
        for i,row in enumerate(self.rows):
            if int(row["id"])==self.selected_id: self.listbox.selection_set(i); self.listbox.see(i); break
        self.refresh_selection()

    def _profile_error(self,exc): self.status_var.set("Profile read failed"); messagebox.showerror("LinkedIn profile",str(exc))
    def _status_busy(self,text): self.status_var.set(text); self.refresh_profile_btn.configure(state="disabled")

    def _run_generation(self,fn,label):
        try:
            pid=self._require(); row=core.get_prospect(pid)
            if row["eliminated"]: raise ValueError("This prospect is in the Elimination Zone. Restore them first.")
        except Exception as exc: messagebox.showerror("Generation",str(exc)); return
        self.status_var.set(f"{label}…")
        def worker():
            try: draft=fn(pid); self.after(0,lambda:self._generation_done(draft,label))
            except Exception as exc: self.after(0,lambda:self._generation_error(exc))
        threading.Thread(target=worker,daemon=True).start()

    def _generation_done(self,draft,label): self.editor.delete("1.0","end"); self.editor.insert("1.0",draft); self.status_var.set(f"{label} ready. Nothing sent automatically."); self.refresh_selection()
    def _generation_error(self,exc): self.status_var.set("Generation failed"); messagebox.showerror("Generation failed",str(exc)); self.refresh_selection()
    def generate_initial(self): self._run_generation(core.generate_initial,"Initial message")
    def generate_reply(self): self._run_generation(core.generate_reply,"Reply")

    def save_message(self,direction):
        try:
            pid=self._require(); core.add_message(pid,direction,self.editor.get("1.0","end")); self.clear_editor(); self.refresh_all(); self._select_current(); self.status_var.set("Conversation updated.")
        except Exception as exc: messagebox.showerror("Save message",str(exc))

    def eliminate(self):
        try:
            pid=self._require(); reason=simpledialog.askstring("Elimination Zone","Reason (optional):",parent=self)
            if reason is None: return
            core.set_eliminated(pid,True,reason); self.refresh_all(); self._select_current()
        except Exception as exc: messagebox.showerror("Elimination Zone",str(exc))

    def restore(self):
        try: core.set_eliminated(self._require(),False); self.refresh_all(); self._select_current()
        except Exception as exc: messagebox.showerror("Restore",str(exc))

    def clear_editor(self): self.editor.delete("1.0","end")


if __name__ == "__main__":
    core.init_db(); LinkedInApp().mainloop()
