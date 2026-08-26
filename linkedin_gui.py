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
    def __init__(self) -> None:
        super().__init__()
        self.title("LinkedIn Conversation CRM")
        self.geometry("1450x900")
        self.minsize(1150, 720)
        self.selected_id: int | None = None
        self.rows = []
        self.category = "all"
        self._ensure_schema()
        self._build()
        self.refresh_all()

    def _ensure_schema(self) -> None:
        # linkedin_agent.init_db creates/migrates the required columns.
        core.init_db()

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        side = ttk.Frame(self, padding=12)
        side.grid(row=0, column=0, sticky="nsew")
        side.columnconfigure(0, weight=1)

        ttk.Label(side, text="LinkedIn CRM", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(side, text="Prospect + conversation pipeline", font=("Segoe UI", 10)).grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )

        self.category_buttons: dict[str, ttk.Button] = {}
        for r, key in enumerate(LABELS, start=2):
            button = ttk.Button(side, text=LABELS[key], command=lambda k=key: self.set_category(k))
            button.grid(row=r, column=0, sticky="ew", pady=2)
            self.category_buttons[key] = button

        ttk.Separator(side).grid(row=8, column=0, sticky="ew", pady=12)
        ttk.Button(side, text="+ Add prospect by URL", command=self.add_by_url).grid(
            row=9, column=0, sticky="ew", pady=(0, 6)
        )
        ttk.Button(side, text="Refresh", command=self.refresh_all).grid(
            row=10, column=0, sticky="ew", pady=(0, 10)
        )

        legend = ttk.LabelFrame(side, text="Stage colors", padding=8)
        legend.grid(row=11, column=0, sticky="ew")
        for r, (key, text) in enumerate([
            ("not_contacted", "Not contacted"),
            ("contacted", "Waiting for reply"),
            ("needs_reply", "Needs your reply"),
            ("active", "Active conversation"),
            ("eliminated", "Elimination Zone"),
        ]):
            tk.Label(
                legend,
                text=f"● {text}",
                fg=COLORS[key],
                anchor="w",
                font=("Segoe UI", 9, "bold"),
            ).grid(row=r, column=0, sticky="w")

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
        search.bind("<KeyRelease>", lambda _event: self.refresh_list())
        self.count_var = tk.StringVar(value="0 prospects")
        ttk.Label(top, textvariable=self.count_var).grid(row=0, column=1)

        self.title_var = tk.StringVar(value="Select a prospect")
        ttk.Label(main, textvariable=self.title_var, font=("Segoe UI", 17, "bold")).grid(
            row=1, column=0, sticky="w", pady=(12, 2)
        )

        self.step_labels: dict[str, tk.Label] = {}
        stepbar = ttk.Frame(main)
        stepbar.grid(row=2, column=0, sticky="ew", pady=(5, 10))
        for c in range(5):
            stepbar.columnconfigure(c, weight=1)
        for c, (key, text) in enumerate([
            ("not_contacted", "1 · Not contacted"),
            ("contacted", "2 · Contacted"),
            ("needs_reply", "3 · Needs reply"),
            ("active", "4 · Active"),
            ("eliminated", "X · Eliminated"),
        ]):
            label = tk.Label(
                stepbar,
                text=text,
                padx=7,
                pady=7,
                bg="#e8e8e8",
                fg="#555",
                font=("Segoe UI", 9, "bold"),
            )
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
        ttk.Label(details, textvariable=self.action_var, font=("Segoe UI", 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )

        actions = ttk.Frame(main)
        actions.grid(row=5, column=0, sticky="ew", pady=8)
        self.open_btn = ttk.Button(actions, text="Open LinkedIn", command=self.open_profile, state="disabled")
        self.open_btn.pack(side="left", padx=(0, 5))
        self.read_profile_btn = ttk.Button(actions, text="Read profile", command=self.read_profile, state="disabled")
        self.read_profile_btn.pack(side="left", padx=5)
        self.initial_btn = ttk.Button(actions, text="Generate initial", command=self.generate_initial, state="disabled")
        self.initial_btn.pack(side="left", padx=5)
        self.reply_btn = ttk.Button(actions, text="Generate reply", command=self.generate_reply, state="disabled")
        self.reply_btn.pack(side="left", padx=5)
        self.eliminate_btn = ttk.Button(actions, text="Eliminate", command=self.eliminate, state="disabled")
        self.eliminate_btn.pack(side="left", padx=(18, 5))
        self.restore_btn = ttk.Button(actions, text="Restore", command=self.restore, state="disabled")
        self.restore_btn.pack(side="left", padx=5)

        conversation = ttk.LabelFrame(main, text="Real conversation", padding=8)
        conversation.grid(row=6, column=0, sticky="nsew")
        conversation.columnconfigure(0, weight=1)
        conversation.rowconfigure(0, weight=1)
        self.history = tk.Text(conversation, height=10, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.history.grid(row=0, column=0, sticky="nsew")

        composer = ttk.LabelFrame(main, text="Draft / exact message input", padding=8)
        composer.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        composer.columnconfigure(0, weight=1)
        self.editor = tk.Text(composer, height=7, wrap="word", font=("Segoe UI", 10))
        self.editor.grid(row=0, column=0, sticky="ew")
        bar = ttk.Frame(composer)
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(bar, text="Save as sent", command=lambda: self.save_message("outbound")).pack(side="left")
        ttk.Button(bar, text="Save prospect reply", command=lambda: self.save_message("inbound")).pack(side="left", padx=8)
        ttk.Button(bar, text="Clear", command=self.clear_editor).pack(side="left")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status_var, relief="sunken", anchor="w").grid(
            row=8, column=0, sticky="ew", pady=(8, 0)
        )

    def set_category(self, category: str) -> None:
        self.category = category
        self.refresh_list()

    def _state(self, row) -> tuple[str, str]:
        if row["eliminated"]:
            return "eliminated", "Do not contact"
        messages = core.get_messages(int(row["id"]))
        if not messages:
            return "not_contacted", "No communication yet"
        if messages[-1]["direction"] == "inbound":
            return "needs_reply", "Your turn to reply"
        if any(message["direction"] == "inbound" for message in messages):
            return "active", "Waiting for prospect"
        return "contacted", "Waiting for prospect"

    def _matches(self, stage: str) -> bool:
        return self.category == "all" or self.category == stage

    def refresh_counts(self) -> None:
        rows = core.search_prospects(limit=100000)
        counts = {key: 0 for key in LABELS if key != "all"}
        for row in rows:
            stage, _ = self._state(row)
            counts[stage] += 1
        self.category_buttons["all"].configure(text=f"All ({len(rows)})")
        for key in counts:
            self.category_buttons[key].configure(text=f"{LABELS[key]} ({counts[key]})")

    def refresh_list(self) -> None:
        query = self.search_var.get().strip()
        source = core.search_prospects(query=query, limit=100000)
        self.rows = [row for row in source if self._matches(self._state(row)[0])]
        self.listbox.delete(0, "end")
        badges = {
            "not_contacted": "NEW",
            "contacted": "WAIT",
            "needs_reply": "REPLY",
            "active": "ACTIVE",
            "eliminated": "X",
        }
        for index, row in enumerate(self.rows):
            stage, _ = self._state(row)
            relation = " · Not connected" if row["relationship"] != "connected" else ""
            company = f" — {row['company']}" if row["company"] else ""
            text = f"[{badges[stage]}] {row['first_name']} {row['last_name']}{company}{relation}"
            self.listbox.insert("end", text)
            self.listbox.itemconfig(index, foreground=COLORS[stage])
        self.count_var.set(f"{len(self.rows)} shown")

    def refresh_all(self) -> None:
        self.refresh_counts()
        self.refresh_list()
        self.refresh_selection()

    def select_prospect(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if selection:
            self.selected_id = int(self.rows[selection[0]]["id"])
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

        stage, action = self._state(row)
        relation = "Connected" if row["relationship"] == "connected" else "Not connected"
        source = "CSV connection" if row["source"] == "connections_csv" else "Added from profile URL"
        role = row["position"] or row["profile_headline"] or "Unknown role"
        company = row["company"] or "Unknown company"
        self.title_var.set(f"{row['first_name']} {row['last_name']}")
        self.info_var.set(
            f"{role}  ·  {company}  ·  {relation}  ·  {source}\n{row['url']}"
        )
        self.action_var.set(f"STEP: {LABELS[stage]}   |   {action}")
        for key, label in self.step_labels.items():
            active = key == stage
            label.configure(
                bg=COLORS[key] if active else "#e8e8e8",
                fg="white" if active else "#555",
            )

        for button in (
            self.open_btn,
            self.read_profile_btn,
            self.initial_btn,
            self.eliminate_btn,
            self.restore_btn,
        ):
            button.configure(state="normal")
        self.eliminate_btn.configure(state="disabled" if row["eliminated"] else "normal")
        self.restore_btn.configure(state="normal" if row["eliminated"] else "disabled")
        has_inbound = any(m["direction"] == "inbound" for m in core.get_messages(self.selected_id))
        self.reply_btn.configure(state="normal" if has_inbound and not row["eliminated"] else "disabled")
        self._show_history()
        self.clear_editor()

    def _clear_selected_ui(self) -> None:
        self.title_var.set("Select a prospect")
        self.info_var.set("Select someone from the list")
        self.action_var.set("")
        for button in (
            self.open_btn,
            self.read_profile_btn,
            self.initial_btn,
            self.reply_btn,
            self.eliminate_btn,
            self.restore_btn,
        ):
            button.configure(state="disabled")
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        self.history.insert("end", "No prospect selected.\n")
        self.history.configure(state="disabled")
        self.clear_editor()
        for label in self.step_labels.values():
            label.configure(bg="#e8e8e8", fg="#555")

    def _show_history(self) -> None:
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        rows = core.get_messages(self.selected_id) if self.selected_id else []
        if not rows:
            self.history.insert("end", "No real messages recorded yet.\n")
        for row in rows:
            who = "YOU" if row["direction"] == "outbound" else "PROSPECT"
            self.history.insert("end", f"{who} · {row['created_at']}\n{row['content']}\n\n")
        self.history.configure(state="disabled")

    def _require(self) -> int:
        if self.selected_id is None:
            raise ValueError("Select a prospect first")
        return self.selected_id

    def open_profile(self) -> None:
        try:
            row = core.get_prospect(self._require())
            if row is None:
                raise ValueError("Prospect not found")
            core.open_profile(row["url"])
            self.status_var.set("Profile opened in your browser.")
        except Exception as exc:
            messagebox.showerror("Open LinkedIn", str(exc))

    def add_by_url(self) -> None:
        url = simpledialog.askstring(
            "Add prospect by LinkedIn URL",
            "Paste the full LinkedIn profile URL:",
            parent=self,
        )
        if not url:
            return
        self._set_busy(True, "Reading LinkedIn profile…")

        def worker() -> None:
            try:
                profile = fetch_profile(url)
                prospect_id = core.add_direct_profile(profile)
                self.after(0, lambda pid=prospect_id: self._profile_done(pid, "Prospect added and profile captured."))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: self._profile_error(error))

        threading.Thread(target=worker, daemon=True).start()

    def read_profile(self) -> None:
        try:
            prospect_id = self._require()
            row = core.get_prospect(prospect_id)
            if row is None:
                raise ValueError("Prospect not found")
        except Exception as exc:
            messagebox.showerror("Profile", str(exc))
            return

        self._set_busy(True, "Reading LinkedIn profile…")

        def worker() -> None:
            try:
                profile = fetch_profile(row["url"])
                pid = core.add_direct_profile(profile)
                self.after(0, lambda pid=pid: self._profile_done(pid, "Profile refreshed."))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: self._profile_error(error))

        threading.Thread(target=worker, daemon=True).start()

    def _profile_done(self, prospect_id: int, message: str) -> None:
        self._set_busy(False, message)
        self.selected_id = prospect_id
        self.refresh_all()
        self._select_current()

    def _profile_error(self, error: str) -> None:
        self._set_busy(False, "Profile read failed")
        messagebox.showerror("LinkedIn profile", error)

    def _set_busy(self, busy: bool, message: str) -> None:
        self.status_var.set(message)
        state = "disabled" if busy else "normal"
        self.open_btn.configure(state=state if self.selected_id is not None else "disabled")
        self.read_profile_btn.configure(state="disabled" if busy else ("normal" if self.selected_id is not None else "disabled"))
        self.initial_btn.configure(state="disabled" if busy else ("normal" if self.selected_id is not None else "disabled"))
        self.reply_btn.configure(state="disabled" if busy else "disabled")

    def _run_generation(self, fn, label: str) -> None:
        try:
            prospect_id = self._require()
            row = core.get_prospect(prospect_id)
            if row is not None and row["eliminated"]:
                raise ValueError("This prospect is in the Elimination Zone. Restore them first.")
        except Exception as exc:
            messagebox.showerror("Generation", str(exc))
            return

        self._set_busy(True, f"{label}…")

        def worker() -> None:
            try:
                draft = fn(prospect_id)
                self.after(0, lambda draft=draft: self._generation_done(draft, label))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda error=error: self._generation_error(error))

        threading.Thread(target=worker, daemon=True).start()

    def _generation_done(self, draft: str, label: str) -> None:
        self._set_busy(False, f"{label} ready. Nothing was sent automatically.")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", draft)
        self.refresh_selection()

    def _generation_error(self, error: str) -> None:
        self._set_busy(False, "Generation failed")
        messagebox.showerror("Generation failed", error)

    def generate_initial(self) -> None:
        self._run_generation(core.generate_initial, "Initial message")

    def generate_reply(self) -> None:
        self._run_generation(core.generate_reply, "Reply")

    def save_message(self, direction: str) -> None:
        try:
            core.add_message(self._require(), direction, self.editor.get("1.0", "end"))
            self.clear_editor()
            self.refresh_all()
            self._select_current()
            self.status_var.set("Conversation updated.")
        except Exception as exc:
            messagebox.showerror("Save message", str(exc))

    def eliminate(self) -> None:
        try:
            reason = simpledialog.askstring(
                "Elimination Zone",
                "Why do you not want to communicate with this prospect? (optional)",
                parent=self,
            )
            if reason is None:
                return
            core.set_eliminated(self._require(), True, reason)
            self.refresh_all()
            self.status_var.set("Prospect moved to the Elimination Zone.")
        except Exception as exc:
            messagebox.showerror("Elimination Zone", str(exc))

    def restore(self) -> None:
        try:
            core.set_eliminated(self._require(), False)
            self.refresh_all()
            self.status_var.set("Prospect restored to the normal pipeline.")
        except Exception as exc:
            messagebox.showerror("Restore", str(exc))

    def clear_editor(self) -> None:
        self.editor.delete("1.0", "end")

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


if __name__ == "__main__":
    core.init_db()
    LinkedInApp().mainloop()
