"""Minimal GUI for the one-prospect-at-a-time LinkedIn assistant."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import linkedin_agent as core


class LinkedInApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("LinkedIn Conversation Assistant")
        self.geometry("1180x760")
        self.minsize(980, 650)
        self.selected_id: int | None = None
        self.rows = []
        self._build()
        self.refresh_prospects()

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self, padding=12)
        left.grid(row=0, column=0, sticky="nsew")
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Prospects", font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        self.search_var = tk.StringVar()
        search = ttk.Entry(left, textvariable=self.search_var)
        search.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        search.bind("<KeyRelease>", lambda _e: self.refresh_prospects())

        self.listbox = tk.Listbox(left, activestyle="dotbox", font=("Segoe UI", 10), exportselection=False)
        self.listbox.grid(row=2, column=0, sticky="nsew")
        self.listbox.bind("<<ListboxSelect>>", self.select_prospect)

        right = ttk.Frame(self, padding=(0, 12, 12, 12))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        self.profile_var = tk.StringVar(value="Select a prospect")
        ttk.Label(right, textvariable=self.profile_var, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")

        actions = ttk.Frame(right)
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        self.open_btn = ttk.Button(actions, text="Open LinkedIn", command=self.open_profile, state="disabled")
        self.open_btn.pack(side="left", padx=(0, 6))
        self.initial_btn = ttk.Button(actions, text="Generate initial message", command=self.generate_initial, state="disabled")
        self.initial_btn.pack(side="left", padx=6)
        self.reply_btn = ttk.Button(actions, text="Generate reply", command=self.generate_reply, state="disabled")
        self.reply_btn.pack(side="left", padx=6)

        conversation = ttk.LabelFrame(right, text="Real conversation", padding=8)
        conversation.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        conversation.columnconfigure(0, weight=1)
        self.history = tk.Text(conversation, height=12, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.history.grid(row=0, column=0, sticky="ew")

        composer = ttk.Frame(right)
        composer.grid(row=3, column=0, sticky="nsew")
        composer.columnconfigure(0, weight=1)
        composer.rowconfigure(1, weight=1)
        ttk.Label(composer, text="Draft / message input").grid(row=0, column=0, sticky="w")
        self.editor = tk.Text(composer, wrap="word", font=("Segoe UI", 11))
        self.editor.grid(row=1, column=0, sticky="nsew", pady=(5, 8))

        buttons = ttk.Frame(composer)
        buttons.grid(row=2, column=0, sticky="ew")
        ttk.Button(buttons, text="Save as sent", command=lambda: self.save_message("outbound")).pack(side="left")
        ttk.Button(buttons, text="Save prospect reply", command=lambda: self.save_message("inbound")).pack(side="left", padx=8)
        ttk.Button(buttons, text="Clear", command=self.clear_editor).pack(side="left")

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(right, textvariable=self.status_var, relief="sunken", anchor="w").grid(row=4, column=0, sticky="ew", pady=(8, 0))

    def refresh_prospects(self) -> None:
        self.rows = core.search_prospects(self.search_var.get())
        self.listbox.delete(0, "end")
        for row in self.rows:
            company = f" — {row['company']}" if row['company'] else ""
            self.listbox.insert("end", f"{row['first_name']} {row['last_name']}{company}")

    def select_prospect(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        row = self.rows[selection[0]]
        self.selected_id = int(row["id"])
        self.profile_var.set(
            f"{row['first_name']} {row['last_name']} · {row['position'] or 'Unknown role'} · {row['company'] or 'Unknown company'}"
        )
        for button in (self.open_btn, self.initial_btn, self.reply_btn):
            button.configure(state="normal")
        self._show_history()
        self.clear_editor()
        self.status_var.set(row["url"])

    def _require_selection(self) -> int:
        if self.selected_id is None:
            raise ValueError("Select a prospect first")
        return self.selected_id

    def _show_history(self) -> None:
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        rows = core.get_messages(self.selected_id) if self.selected_id else []
        if not rows:
            self.history.insert("end", "No messages recorded yet.\n")
        else:
            for row in rows:
                label = "YOU" if row["direction"] == "outbound" else "PROSPECT"
                self.history.insert("end", f"{label}:\n{row['content']}\n\n")
        self.history.configure(state="disabled")

    def open_profile(self) -> None:
        try:
            row = core.get_prospect(self._require_selection())
            if row is None:
                raise ValueError("Prospect not found")
            core.open_profile(row["url"])
            self.status_var.set(f"Opened {row['url']}. Work manually in your browser.")
        except Exception as exc:
            messagebox.showerror("Open LinkedIn", str(exc))

    def _run_generation(self, fn, label: str) -> None:
        try:
            prospect_id = self._require_selection()
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
            label = "sent" if direction == "outbound" else "reply saved"
            self.status_var.set(f"Conversation updated: {label}.")
        except Exception as exc:
            messagebox.showerror("Save message", str(exc))

    def clear_editor(self) -> None:
        self.editor.delete("1.0", "end")


if __name__ == "__main__":
    core.init_db()
    LinkedInApp().mainloop()
