"""Visual LinkedIn prospect and conversation CRM."""

from __future__ import annotations

import sqlite3
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import linkedin_agent as core


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
    def __init__(self) -> None:
        super().__init__()
        self.title("LinkedIn Conversation CRM")
        self.geometry("1380x860")
        self.minsize(1100, 700)
        self.selected_id: int | None = None
        self.rows = []
        self.category = "all"
        self._ensure_crm_columns()
        self._build()
        self.refresh_all()

    def _ensure_crm_columns(self) -> None:
        with sqlite3.connect(core.DB_PATH) as db:
            cols = {r[1] for r in db.execute("PRAGMA table_info(prospects)").fetchall()}
            migrations = {
                "eliminated": "ALTER TABLE prospects ADD COLUMN eliminated INTEGER NOT NULL DEFAULT 0",
                "elimination_reason": "ALTER TABLE prospects ADD COLUMN elimination_reason TEXT",
                "eliminated_at": "ALTER TABLE prospects ADD COLUMN eliminated_at TEXT",
            }
            for col, sql in migrations.items():
                if col not in cols:
                    db.execute(sql)

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, padding=12)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(7, weight=1)

        ttk.Label(sidebar, text="LinkedIn CRM", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(sidebar, text="Prospect pipeline", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=(0, 12))

        self.category_buttons: dict[str, ttk.Button] = {}
        for i, key in enumerate(LABELS):
            btn = ttk.Button(sidebar, text=LABELS[key], command=lambda k=key: self.set_category(k))
            btn.grid(row=2 + i, column=0, sticky="ew", pady=2)
            self.category_buttons[key] = btn

        ttk.Separator(sidebar).grid(row=8, column=0, sticky="ew", pady=12)
        legend = ttk.LabelFrame(sidebar, text="Stage colors", padding=8)
        legend.grid(row=9, column=0, sticky="ew")
        for r, (key, text) in enumerate([
            ("not_contacted", "Not contacted"),
            ("contacted", "Contacted / waiting"),
            ("needs_reply", "Prospect replied"),
            ("active", "Two-way conversation"),
            ("eliminated", "Elimination Zone"),
        ]):
            label = tk.Label(legend, text=f"● {text}", anchor="w", fg=COLORS[key], font=("Segoe UI", 9, "bold"))
            label.grid(row=r, column=0, sticky="w")

        main = ttk.Frame(self, padding=(4, 12, 12, 12))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)
        main.rowconfigure(5, weight=1)

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self.search_var = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.search_var, font=("Segoe UI", 11))
        search.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        search.insert(0, "Search prospect, company...")
        search.bind("<FocusIn>", self._clear_search_placeholder)
        search.bind("<KeyRelease>", lambda _e: self.refresh_list())
        self.count_var = tk.StringVar(value="0 prospects")
        ttk.Label(top, textvariable=self.count_var).grid(row=0, column=1, sticky="e")

        self.stage_title = tk.StringVar(value="Select a prospect")
        ttk.Label(main, textvariable=self.stage_title, font=("Segoe UI", 17, "bold")).grid(row=1, column=0, sticky="w", pady=(12, 2))

        stepbar = ttk.Frame(main)
        stepbar.grid(row=2, column=0, sticky="ew", pady=(4, 10))
        for c in range(4):
            stepbar.columnconfigure(c, weight=1)
        self.step_labels = {}
        for c, (key, text) in enumerate([
            ("not_contacted", "1 · Not contacted"),
            ("contacted", "2 · Contacted"),
            ("active", "3 · Conversation active"),
            ("eliminated", "X · Elimination Zone"),
        ]):
            lbl = tk.Label(stepbar, text=text, padx=8, pady=7, bg="#e8e8e8", fg="#555", font=("Segoe UI", 9, "bold"))
            lbl.grid(row=0, column=c, sticky="ew", padx=2)
            self.step_labels[key] = lbl

        list_frame = ttk.Frame(main)
        list_frame.grid(row=3, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(list_frame, activestyle="dotbox", font=("Segoe UI", 10), exportselection=False)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self.select_prospect)

        details = ttk.LabelFrame(main, text="Selected prospect", padding=10)
        details.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        details.columnconfigure(1, weight=1)
        self.info_var = tk.StringVar(value="Select someone from the list")
        ttk.Label(details, textvariable=self.info_var, font=("Segoe UI", 10)).grid(row=0, column=0, columnspan=2, sticky="w")
        self.action_var = tk.StringVar(value="")
        ttk.Label(details, textvariable=self.action_var, font=("Segoe UI", 10, "bold")).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        actions = ttk.Frame(main)
        actions.grid(row=5, column=0, sticky="ew", pady=(8, 8))
        for c in range(6):
            actions.columnconfigure(c, weight=0)
        self.open_btn = ttk.Button(actions, text="Open LinkedIn", command=self.open_profile, state="disabled")
        self.open_btn.grid(row=0, column=0, padx=(0, 6))
        self.initial_btn = ttk.Button(actions, text="Generate initial", command=self.generate_initial, state="disabled")
        self.initial_btn.grid(row=0, column=1, padx=3)
        self.reply_btn = ttk.Button(actions, text="Generate reply", command=self.generate_reply, state="disabled")
        self.reply_btn.grid(row=0, column=2, padx=3)
        self.eliminate_btn = ttk.Button(actions, text="Move to Elimination Zone", command=self.eliminate, state="disabled")
        self.eliminate_btn.grid(row=0, column=3, padx=12)
        self.restore_btn = ttk.Button(actions, text="Restore", command=self.restore, state="disabled")
        self.restore_btn.grid(row=0, column=4, padx=3)

        conversation = ttk.LabelFrame(main, text="Real conversation", padding=8)
        conversation.grid(row=6, column=0, sticky="nsew", pady=(0, 8))
        conversation.columnconfigure(0, weight=1)
        conversation.rowconfigure(0, weight=1)
        self.history = tk.Text(conversation, height=10, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.history.grid(row=0, column=0, sticky="nsew")

        composer = ttk.LabelFrame(main, text="Draft / exact message input", padding=8)
        composer.grid(row=7, column=0, sticky="nsew")
        composer.columnconfigure(0, weight=1)
        composer.rowconfigure(0, weight=1)
        self.editor = tk.Text(composer, height=8, wrap="word", font=("Segoe UI", 10))
        self.editor.grid(row=0, column=0, sticky="nsew")
        buttons = ttk.Frame(composer)
        buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Save as sent", command=lambda: self.save_message("outbound")).pack(side="left")
        ttk.Button(buttons, text="Save prospect reply", command=lambda: self.save_message("inbound")).pack(side="left", padx=8)
        ttk.Button(buttons, text="Clear", command=self.clear_editor).pack(side="left")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status_var, relief="sunken", anchor="w").grid(row=8, column=0, sticky="ew", pady=(8, 0))

    def _clear_search_placeholder(self, _event=None) -> None:
        if self.search_var.get() == "Search prospect, company...":
            self.search_var.set("")

    def set_category(self, category: str) -> None:
        self.category = category
        self.refresh_list()

    def refresh_all(self) -> None:
        self.refresh_counts()
        self.refresh_list()
        self.refresh_selection()

    def refresh_counts(self) -> None:
        try:
            rows = core.search_prospects(limit=100000)
            all_count = len(rows)
            counts = {k: 0 for k in LABELS if k != "all"}
            for row in rows:
                stage, action = self._row_state(row)
                if stage == "not_contacted":
                    counts["not_contacted"] += 1
                elif stage == "contacted":
                    counts["contacted"] += 1
                elif stage == "active":
                    counts["active"] += 1
                    if action == "Your turn to reply":
                        counts["needs_reply"] += 1
                elif stage == "eliminated":
                    counts["eliminated"] += 1
            self.category_buttons["all"].configure(text=f"All ({all_count})")
            for k in counts:
                self.category_buttons[k].configure(text=f"{LABELS[k]} ({counts[k]})")
            self.count_var.set(f"{all_count} prospects")
        except Exception as exc:
            self.status_var.set(f"Refresh error: {exc}")

    def refresh_list(self) -> None:
        query = self.search_var.get().strip()
        if query == "Search prospect, company...":
            query = ""
        try:
            source = core.search_prospects(query=query, limit=100000)
            rows = []
            for row in source:
                stage, action = self._row_state(row)
                if self._category_matches(stage, action):
                    rows.append(row)
            self.rows = rows
            self.listbox.delete(0, "end")
            for i, row in enumerate(rows):
                stage, action = self._row_state(row)
                color_key = "needs_reply" if action == "Your turn to reply" else stage
                badge = "REPLY" if action == "Your turn to reply" else ("WAIT" if action == "Waiting for prospect" else ("NEW" if stage == "not_contacted" else ("ACTIVE" if stage == "active" else "ELIMINATED")))
                company = f" — {row['company']}" if row['company'] else ""
                self.listbox.insert("end", f"[{badge}] {row['first_name']} {row['last_name']}{company}")
                self.listbox.itemconfig(i, foreground=COLORS[color_key])
            self.count_var.set(f"{len(rows)} shown")
        except Exception as exc:
            self.status_var.set(f"List error: {exc}")

    def _category_matches(self, stage: str, action: str) -> bool:
        if self.category == "all":
            return True
        if self.category == "not_contacted":
            return stage == "not_contacted"
        if self.category == "contacted":
            return stage == "contacted" and action == "Waiting for prospect"
        if self.category == "needs_reply":
            return action == "Your turn to reply"
        if self.category == "active":
            return stage == "active"
        if self.category == "eliminated":
            return stage == "eliminated"
        return True

    def _row_state(self, row) -> tuple[str, str]:
        if row["eliminated"]:
            return "eliminated", "Do not contact"
        messages = core.get_messages(int(row["id"]))
        if not messages:
            return "not_contacted", "No communication yet"
        has_inbound = any(m["direction"] == "inbound" for m in messages)
        stage = "active" if has_inbound else "contacted"
        action = "Your turn to reply" if messages[-1]["direction"] == "inbound" else "Waiting for prospect"
        return stage, action

    def select_prospect(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        row = self.rows[selection[0]]
        self.selected_id = int(row["id"])
        self.refresh_selection()

    def refresh_selection(self) -> None:
        if self.selected_id is None:
            self._clear_selected_ui()
            return
        row = core.get_prospect(self.selected_id)
        if row is None:
            self.selected_id = None
            self._clear_selected_ui()
            return
        stage, action = self._row_state(row)
        self.stage_title.set(f"{row['first_name']} {row['last_name']}")
        self.info_var.set(
            f"{row['position'] or 'Unknown role'}  ·  {row['company'] or 'Unknown company'}  ·  Connected {row['connected_on'] or 'unknown'}\n{row['url']}"
        )
        self.action_var.set(f"STEP: {LABELS.get(stage, stage)}   |   {action}")
        self._update_stepbar(stage)
        enabled = "normal"
        for button in (self.open_btn, self.initial_btn, self.reply_btn, self.eliminate_btn, self.restore_btn):
            button.configure(state=enabled)
        if row["eliminated"]:
            self.eliminate_btn.configure(state="disabled")
        else:
            self.restore_btn.configure(state="disabled")
        self._show_history()
        self.clear_editor()
        self.status_var.set("Ready")

    def _update_stepbar(self, stage: str) -> None:
        for key, label in self.step_labels.items():
            active = key == stage
            if stage == "active" and key == "active":
                active = True
            if stage == "eliminated" and key == "eliminated":
                active = True
            label.configure(
                bg=COLORS[key] if active else "#e8e8e8",
                fg="white" if active else "#555",
            )

    def _clear_selected_ui(self) -> None:
        self.stage_title.set("Select a prospect")
        self.info_var.set("Select someone from the list")
        self.action_var.set("")
        for button in (self.open_btn, self.initial_btn, self.reply_btn, self.eliminate_btn, self.restore_btn):
            button.configure(state="disabled")
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        self.history.insert("end", "No prospect selected.\n")
        self.history.configure(state="disabled")
        self.clear_editor()
        for label in self.step_labels.values():
            label.configure(bg="#e8e8e8", fg="#555")

    def _require_selection(self) -> int:
        if self.selected_id is None:
            raise ValueError("Select a prospect first")
        return self.selected_id

    def _show_history(self) -> None:
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        rows = core.get_messages(self.selected_id) if self.selected_id else []
        if not rows:
            self.history.insert("end", "No real messages recorded yet.\n")
        else:
            for row in rows:
                label = "YOU" if row["direction"] == "outbound" else "PROSPECT"
                self.history.insert("end", f"{label}  ·  {row['created_at']}\n{row['content']}\n\n")
        self.history.configure(state="disabled")

    def open_profile(self) -> None:
        try:
            row = core.get_prospect(self._require_selection())
            if row is None:
                raise ValueError("Prospect not found")
            core.open_profile(row["url"])
            self.status_var.set("LinkedIn profile opened. Work manually in your browser.")
        except Exception as exc:
            messagebox.showerror("Open LinkedIn", str(exc))

    def _run_generation(self, fn, label: str) -> None:
        try:
            prospect_id = self._require_selection()
            row = core.get_prospect(prospect_id)
            if row is not None and row["eliminated"]:
                raise ValueError("This prospect is in the Elimination Zone. Restore them before generating messages.")
        except Exception as exc:
            messagebox.showerror("Generation", str(exc))
            return
        self.status_var.set(f"{label}…")
        self.initial_btn.configure(state="disabled")
        self.reply_btn.configure(state="disabled")

        def worker():
            try:
                draft = fn(prospect_id)
                self.after(0, lambda: self._generation_done(draft, label))
            except Exception as exc:
                self.after(0, lambda: self._generation_error(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _generation_done(self, draft: str, label: str) -> None:
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", draft)
        self.status_var.set(f"{label} ready. Nothing was sent automatically.")
        self.initial_btn.configure(state="normal")
        self.reply_btn.configure(state="normal")

    def _generation_error(self, exc: Exception) -> None:
        self.status_var.set("Generation failed")
        self.initial_btn.configure(state="normal")
        self.reply_btn.configure(state="normal")
        messagebox.showerror("Generation failed", str(exc))

    def generate_initial(self) -> None:
        self._run_generation(core.generate_initial, "Initial message")

    def generate_reply(self) -> None:
        self._run_generation(core.generate_reply, "Reply")

    def save_message(self, direction: str) -> None:
        try:
            prospect_id = self._require_selection()
            content = self.editor.get("1.0", "end").strip()
            core.add_message(prospect_id, direction, content)
            self._show_history()
            self.clear_editor()
            self.refresh_all()
            self._select_current()
            label = "sent" if direction == "outbound" else "prospect reply saved"
            self.status_var.set(f"Conversation updated: {label}.")
        except Exception as exc:
            messagebox.showerror("Save message", str(exc))

    def _select_current(self) -> None:
        if self.selected_id is None:
            return
        for index, row in enumerate(self.rows):
            if int(row["id"]) == self.selected_id:
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(index)
                self.listbox.see(index)
                self.refresh_selection()
                break

    def eliminate(self) -> None:
        try:
            prospect_id = self._require_selection()
            reason = simpledialog.askstring(
                "Elimination Zone",
                "Why don't you want to communicate with this prospect?\n(optional)",
                parent=self,
            )
            if reason is None:
                return
            core.connect().close()
            with sqlite3.connect(core.DB_PATH) as db:
                db.execute(
                    "UPDATE prospects SET eliminated=1, elimination_reason=?, eliminated_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
                    (reason.strip() or None, prospect_id),
                )
            self.refresh_all()
            self._select_current()
            self.status_var.set("Moved to Elimination Zone. No messages can be generated until restored.")
        except Exception as exc:
            messagebox.showerror("Elimination Zone", str(exc))

    def restore(self) -> None:
        try:
            prospect_id = self._require_selection()
            with sqlite3.connect(core.DB_PATH) as db:
                db.execute(
                    "UPDATE prospects SET eliminated=0, elimination_reason=NULL, eliminated_at=NULL, updated_at=datetime('now') WHERE id=?",
                    (prospect_id,),
                )
            self.refresh_all()
            self._select_current()
            self.status_var.set("Prospect restored to the normal pipeline.")
        except Exception as exc:
            messagebox.showerror("Restore prospect", str(exc))

    def clear_editor(self) -> None:
        self.editor.delete("1.0", "end")


if __name__ == "__main__":
    core.init_db()
    LinkedInApp().mainloop()
