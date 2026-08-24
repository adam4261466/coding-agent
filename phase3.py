"""Run Phase 3 (outreach & conversion engine) from the command line.

Everything touching a real person is manual: you approve messages, you mark
them sent, you paste inbound replies. The engine only prepares work and
records the funnel.

Usage:
    python phase3.py --sync                  # create campaigns from YAML
    python phase3.py --assign --campaign ai_engineers --top 10
    python phase3.py --produce --campaign ai_engineers --limit 5 --model gemma4:31b-cloud
    python phase3.py --approve <message_id>
    python phase3.py --reject <message_id> --note "rewrite"
    python phase3.py --mark-sent --campaign ai_engineers --prospect p_xxx
    python phase3.py --reply --campaign ai_engineers --prospect p_xxx --text "yes tell me more"
    python phase3.py --followups --campaign ai_engineers
    python phase3.py --report [--campaign ai_engineers] [--json]
    python phase3.py --gui                  # Phase 3 CRM
"""

import argparse
import json
import os
import sys
import threading
import traceback
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, Exception):
    pass

# ---------------------------------------------------------------------------
# Inline debugging: prints to stderr and appends to agent_debug.log
# (set AGENT_DEBUG=0 to disable)
# ---------------------------------------------------------------------------
_DEBUG_ON = os.environ.get("AGENT_DEBUG", "1") != "0"
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_debug.log")


def _dbg(msg: str):
    if not _DEBUG_ON:
        return
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        thread = threading.current_thread().name
        line = f"[{ts}] [phase3] [{thread}] {msg}"
        print(line[:4000], file=sys.stderr, flush=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


from linkedin_intelligence.utils import DB_PATH, campaign_config
from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach.campaign import (
    sync_campaigns, eligible_for_campaign, assign_prospects)
from linkedin_intelligence.outreach.pipeline import (
    produce_batch, register_reply)
from linkedin_intelligence.outreach.approval import approve, reject, approval_queue
from linkedin_intelligence.outreach.sequence import mark_sent, make_follow_up_eligible
from linkedin_intelligence.outreach.analytics import overview
from linkedin_intelligence.outreach.attribution import funnel_for_campaign


def _campaign(store, name):
    c = store.get_campaign(name)
    if not c:
        raise ValueError(f"unknown campaign '{name}' - run --sync first")
    return c


class OutreachController:
    """GUI-safe facade over the Phase 3 engine.

    The controller never sends LinkedIn messages. It only:
      - syncs campaigns
      - finds/assigns eligible prospects
      - generates validated drafts
      - approves/rejects drafts
      - records that the human sent a message
      - records/classifies pasted replies
      - prepares follow-ups
      - reads funnel analytics

    GUI code should use this class instead of calling the CLI.

    IMPORTANT: every method below that takes `campaign_name` expects the
    campaign's `campaign_id` (the stable key used in the DB and in
    config/campaigns/*.yaml filenames) - NOT the human-readable
    campaign["name"] from the YAML. Those two are frequently different
    strings, so callers (e.g. the GUI) must resolve the id first.
    """

    def __init__(self, model="gemma4:31b-cloud", base_url="http://localhost:11434"):
        self.model = model
        self.base_url = base_url
        self.store = Store(DB_PATH)

    def close(self):
        try:
            self.store.close()
        except Exception:
            pass

    def campaigns(self):
        return self.store.campaigns()

    def sync(self):
        return sync_campaigns(self.store)

    def eligible(self, campaign_name, top=0):
        c = _campaign(self.store, campaign_name)
        cfg = campaign_config(c)
        min_prio = float(cfg.get("limits", {}).get("min_priority", 40))
        candidates = eligible_for_campaign(
            self.store, c, self.store.prospects(limit=None), min_prio
        )
        if top > 0:
            candidates = candidates[:top]
        return candidates

    def assign(self, campaign_name, top=0):
        c = _campaign(self.store, campaign_name)
        candidates = self.eligible(campaign_name, top)
        if not candidates:
            return []
        return assign_prospects(self.store, c, candidates)

    def produce(self, campaign_name, limit=5):
        c = _campaign(self.store, campaign_name)
        return produce_batch(
            self.store, c, limit=limit, model=self.model, base_url=self.base_url
        )

    def approve(self, message_id):
        return approve(self.store, message_id)

    def reject(self, message_id, note=None):
        return reject(self.store, message_id, note=note)

    def approval_queue(self, campaign_name=None):
        if campaign_name:
            c = _campaign(self.store, campaign_name)
            return approval_queue(self.store, c["campaign_id"])
        return approval_queue(self.store)

    def mark_sent(self, campaign_name, prospect_id):
        c = _campaign(self.store, campaign_name)
        cp = self.store.get_campaign_prospect(c["campaign_id"], prospect_id)
        if not cp:
            raise ValueError(f"prospect '{prospect_id}' is not assigned to campaign '{campaign_name}'")
        return mark_sent(self.store, cp)

    def reply(self, campaign_name, prospect_id, text):
        c = _campaign(self.store, campaign_name)
        return register_reply(
            self.store, c, prospect_id, text, model=self.model
        )

    def followups(self, campaign_name):
        c = _campaign(self.store, campaign_name)
        return make_follow_up_eligible(self.store, c)

    def report(self, campaign_name=None):
        if campaign_name:
            c = _campaign(self.store, campaign_name)
            data = overview(self.store, c["campaign_id"])
            data["funnel"] = funnel_for_campaign(self.store, c["campaign_id"])
            return data

        data = overview(self.store)
        data["funnels"] = {
            c["campaign_id"]: funnel_for_campaign(self.store, c["campaign_id"])
            for c in self.store.campaigns()
        }
        return data


def run_outreach_gui(parent=None, model="gemma4:31b-cloud",
                     base_url="http://localhost:11434"):
    """Launch the Phase 3 Outreach Control Center.

    If parent is supplied, the UI is embedded in that Tkinter window.
    Otherwise a standalone window is created.

    Returns the Tkinter frame/window and controller. The caller owns cleanup.
    """
    import tkinter as tk
    from tkinter import ttk, messagebox

    controller = OutreachController(model=model, base_url=base_url)

    standalone = parent is None
    if standalone:
        root = tk.Tk()
        root.title("LinkedIn Intelligence — Phase 3 Outreach")
        root.geometry("1250x800")
        host = root
    else:
        root = parent
        host = parent

    outer = ttk.Frame(host, padding=10)
    outer.pack(fill="both", expand=True)

    # ---------- top bar ----------
    top = ttk.Frame(outer)
    top.pack(fill="x", pady=(0, 8))

    ttk.Label(top, text="Phase 3 — Outreach Control Center",
              font=("Segoe UI", 16, "bold")).pack(side="left")

    status_var = tk.StringVar(value="Ready")
    ttk.Label(top, textvariable=status_var).pack(side="right")

    # ---------- controls ----------
    controls = ttk.LabelFrame(outer, text="Campaign controls", padding=8)
    controls.pack(fill="x", pady=(0, 8))

    ttk.Label(controls, text="Campaign").grid(row=0, column=0, sticky="w")
    campaign_var = tk.StringVar()
    campaign_box = ttk.Combobox(
        controls, textvariable=campaign_var, state="readonly", width=32
    )
    campaign_box.grid(row=0, column=1, padx=6)

    # Maps the label shown in the dropdown -> the real campaign_id. The
    # dropdown shows a human-readable label, but every controller call below
    # needs the stable campaign_id (campaign["name"] and campaign["campaign_id"]
    # are frequently different strings - looking a campaign up by its display
    # name was the root cause of "sync works but nothing else does").
    campaign_lookup = {}

    def _campaign_label(c):
        name = c.get("name") or c["campaign_id"]
        if name == c["campaign_id"]:
            return name
        return f"{name} ({c['campaign_id']})"

    def selected_campaign_id():
        return campaign_lookup.get(campaign_var.get())

    ttk.Label(controls, text="Top / limit").grid(row=0, column=2, sticky="w")
    limit_var = tk.IntVar(value=5)
    ttk.Spinbox(controls, from_=1, to=500, textvariable=limit_var,
                width=8).grid(row=0, column=3, padx=6)

    ttk.Label(controls, text="Model").grid(row=0, column=4, sticky="w")
    model_var = tk.StringVar(value=model)
    model_box = ttk.Combobox(
        controls, textvariable=model_var, width=24
    )
    model_box.grid(row=0, column=5, padx=6)
    model_box["values"] = [model]

    def set_status(s):
        status_var.set(s)
        host.update_idletasks()

    def refresh_campaigns():
        try:
            cs = controller.campaigns()
            campaign_lookup.clear()
            labels = []
            for c in cs:
                label = _campaign_label(c)
                campaign_lookup[label] = c["campaign_id"]
                labels.append(label)
            campaign_box["values"] = labels
            if labels and campaign_var.get() not in labels:
                campaign_var.set(labels[0])
            set_status(f"{len(labels)} campaign(s)")
        except Exception as exc:
            messagebox.showerror("Campaigns", str(exc))

    def refresh_queue():
        queue.delete("1.0", "end")
        try:
            camp = selected_campaign_id()
            items = controller.approval_queue(camp)
            if not items:
                queue.insert("end", "No messages awaiting human review.\n")
                return
            for m in items:
                queue.insert(
                    "end",
                    f"ID: {m.get('message_id')}\n"
                    f"Prospect: {m.get('prospect_id')}\n"
                    f"Campaign: {m.get('campaign_id')}\n"
                    f"Version: {m.get('version')}\n"
                    f"Status: {m.get('status')}\n"
                    f"Text:\n{m.get('text', '')}\n"
                    + "-" * 80 + "\n"
                )
        except Exception as exc:
            queue.insert("end", f"ERROR: {exc}\n")

    def do_sync():
        def work():
            try:
                set_status("Syncing campaigns...")
                created = controller.sync()
                refresh_campaigns()
                set_status(f"Synced: {len(created)} campaign(s)")
            except Exception as exc:
                messagebox.showerror("Sync failed", str(exc))
                set_status("Error")
        threading.Thread(target=work, daemon=True).start()

    def do_assign():
        camp = selected_campaign_id()
        if not camp:
            messagebox.showwarning("Assign", "Select a campaign first.")
            return
        try:
            n = int(limit_var.get())
        except Exception:
            n = 5

        def work():
            try:
                set_status(f"Finding eligible prospects for {camp}...")
                assigned = controller.assign(camp, n)
                set_status(f"Assigned {len(assigned)} prospect(s)")
                refresh_queue()
            except Exception as exc:
                messagebox.showerror("Assign failed", str(exc))
                set_status("Error")
        threading.Thread(target=work, daemon=True).start()

    def do_produce():
        camp = selected_campaign_id()
        if not camp:
            messagebox.showwarning("Generate", "Select a campaign first.")
            return
        try:
            n = int(limit_var.get())
        except Exception:
            n = 5

        def work():
            try:
                set_status(f"Generating drafts for {camp}...")
                summary = controller.produce(camp, n)
                set_status(
                    f"Generated {summary.get('generated', 0)} draft(s)"
                )
                refresh_queue()
                messagebox.showinfo(
                    "Message generation",
                    f"Started: {summary.get('started', 0)}\n"
                    f"Generated: {summary.get('generated', 0)}"
                )
            except Exception as exc:
                messagebox.showerror("Generation failed", str(exc))
                set_status("Error")
        threading.Thread(target=work, daemon=True).start()

    def selected_message_id():
        # Prefer a selected ID in the queue text; otherwise ask explicitly.
        import re
        selected = queue.tag_ranges("sel")
        text = queue.get(
            selected[0], selected[1]
        ) if selected else queue.get("1.0", "end")
        m = re.search(r"ID:\s*(\S+)", text)
        return m.group(1) if m else None

    def do_approve():
        mid = selected_message_id()
        if not mid:
            messagebox.showwarning(
                "Approve", "Select/highlight a message block first."
            )
            return
        try:
            result = controller.approve(mid)
            set_status(f"Approved {mid}")
            refresh_queue()
            messagebox.showinfo(
                "Approved",
                f"Message approved for {result.get('prospect_id')}"
            )
        except Exception as exc:
            messagebox.showerror("Approval failed", str(exc))

    def do_reject():
        mid = selected_message_id()
        if not mid:
            messagebox.showwarning(
                "Reject", "Select/highlight a message block first."
            )
            return
        note = note_var.get().strip() or "Rejected from GUI"
        try:
            controller.reject(mid, note)
            set_status(f"Rejected {mid}")
            refresh_queue()
        except Exception as exc:
            messagebox.showerror("Rejection failed", str(exc))

    def do_sent():
        camp = selected_campaign_id()
        prospect = prospect_var.get().strip()
        if not camp or not prospect:
            messagebox.showwarning(
                "Mark sent", "Select a campaign and enter a prospect ID."
            )
            return
        try:
            controller.mark_sent(camp, prospect)
            set_status(f"Marked sent: {prospect}")
            refresh_report()
        except Exception as exc:
            messagebox.showerror("Mark sent failed", str(exc))

    def do_reply():
        camp = selected_campaign_id()
        prospect = prospect_var.get().strip()
        reply_text = reply_box.get("1.0", "end").strip()
        if not camp or not prospect or not reply_text:
            messagebox.showwarning(
                "Reply", "Campaign, prospect ID and reply text are required."
            )
            return

        def work():
            try:
                set_status("Classifying reply...")
                result = controller.reply(camp, prospect, reply_text)
                cls = result.get("classification", {})
                set_status("Reply recorded")
                refresh_report()
                messagebox.showinfo(
                    "Reply classification",
                    f"Intent: {cls.get('intent')}\n"
                    f"Objection: {cls.get('objection')}\n"
                    f"Commercial intent: {cls.get('commercial_intent')}"
                )
            except Exception as exc:
                messagebox.showerror("Reply failed", str(exc))
                set_status("Error")
        threading.Thread(target=work, daemon=True).start()

    def do_followups():
        camp = selected_campaign_id()
        if not camp:
            messagebox.showwarning("Follow-ups", "Select a campaign first.")
            return
        try:
            moved = controller.followups(camp)
            set_status(f"{len(moved)} follow-up(s) eligible")
            refresh_report()
        except Exception as exc:
            messagebox.showerror("Follow-ups failed", str(exc))

    def refresh_report():
        report_box.delete("1.0", "end")
        try:
            data = controller.report(selected_campaign_id())
            report_box.insert(
                "end", json.dumps(data, ensure_ascii=False, indent=2)
            )
        except Exception as exc:
            report_box.insert("end", f"ERROR: {exc}\n")

    # ---------- action buttons ----------
    buttons = ttk.Frame(controls)
    buttons.grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))

    ttk.Button(buttons, text="Sync campaigns",
               command=do_sync).pack(side="left", padx=3)
    ttk.Button(buttons, text="Assign prospects",
               command=do_assign).pack(side="left", padx=3)
    ttk.Button(buttons, text="Generate messages",
               command=do_produce).pack(side="left", padx=3)
    ttk.Button(buttons, text="Refresh queue",
               command=refresh_queue).pack(side="left", padx=3)
    ttk.Button(buttons, text="Refresh analytics",
               command=refresh_report).pack(side="left", padx=3)

    # Re-resolve the campaign_id whenever the user picks a different label.
    campaign_box.bind("<<ComboboxSelected>>",
                      lambda _e: (refresh_queue(), refresh_report()))

    # ---------- message review ----------
    review = ttk.PanedWindow(outer, orient="horizontal")
    review.pack(fill="both", expand=True)

    left = ttk.Frame(review, padding=5)
    right = ttk.Frame(review, padding=5)
    review.add(left, weight=3)
    review.add(right, weight=2)

    ttk.Label(left, text="Messages awaiting human review",
              font=("Segoe UI", 11, "bold")).pack(anchor="w")

    queue = tk.Text(left, wrap="word", height=24)
    queue.pack(fill="both", expand=True)

    note_row = ttk.Frame(left)
    note_row.pack(fill="x", pady=5)
    ttk.Label(note_row, text="Review note").pack(side="left")
    note_var = tk.StringVar()
    ttk.Entry(note_row, textvariable=note_var).pack(
        side="left", fill="x", expand=True, padx=5
    )

    review_buttons = ttk.Frame(left)
    review_buttons.pack(fill="x")
    ttk.Button(review_buttons, text="✓ Approve",
               command=do_approve).pack(side="left", padx=3)
    ttk.Button(review_buttons, text="✗ Reject",
               command=do_reject).pack(side="left", padx=3)

    # ---------- conversation / funnel ----------
    ttk.Label(right, text="Conversation & funnel",
              font=("Segoe UI", 11, "bold")).pack(anchor="w")

    prospect_row = ttk.Frame(right)
    prospect_row.pack(fill="x", pady=5)
    ttk.Label(prospect_row, text="Prospect ID").pack(side="left")
    prospect_var = tk.StringVar()
    ttk.Entry(prospect_row, textvariable=prospect_var).pack(
        side="left", fill="x", expand=True, padx=5
    )
    ttk.Button(prospect_row, text="Mark sent",
               command=do_sent).pack(side="left")

    ttk.Label(right, text="Inbound reply (paste it here)").pack(anchor="w")
    reply_box = tk.Text(right, height=7, wrap="word")
    reply_box.pack(fill="x")

    reply_buttons = ttk.Frame(right)
    reply_buttons.pack(fill="x", pady=5)
    ttk.Button(reply_buttons, text="Record / classify reply",
               command=do_reply).pack(side="left", padx=3)
    ttk.Button(reply_buttons, text="Make follow-ups eligible",
               command=do_followups).pack(side="left", padx=3)

    ttk.Label(right, text="Analytics").pack(anchor="w", pady=(8, 0))
    report_box = tk.Text(right, height=18, wrap="word")
    report_box.pack(fill="both", expand=True)

    refresh_campaigns()
    refresh_queue()
    refresh_report()

    if standalone:
        def on_close():
            controller.close()
            root.destroy()
        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()

    return outer, controller


def cmd_sync(store):
    created = sync_campaigns(store)
    if not created:
        print("[outreach] campaigns already synchronized; nothing new to create")
        return
    for campaign in created:
        print(
            f"[outreach] {campaign['campaign_id']} "
            f"({campaign.get('name','')}, {campaign.get('strategy','')}) "
            f"- {campaign.get('status','')}"
        )


def cmd_assign(store, campaign_name, top):
    campaign = _campaign(store, campaign_name)
    cfg = campaign_config(campaign) or {}
    try:
        min_priority = float((cfg.get("limits") or {}).get("min_priority", 40))
    except (TypeError, ValueError):
        min_priority = 40.0
    candidates = eligible_for_campaign(
        store, campaign, store.prospects(limit=None), min_priority
    )
    if top > 0:
        candidates = candidates[:top]
    if not candidates:
        print(f"[outreach] no eligible prospects for {campaign_name}")
        return
    print(f"[outreach] eligible candidates: {len(candidates)}")
    for prospect in candidates:
        print(f"  {prospect.get('prospect_id')} priority={prospect.get('outreach_priority','?')}")
    assigned = assign_prospects(store, campaign, candidates)
    print(f"[outreach] assigned {len(assigned)} prospect(s) to {campaign_name}")


def cmd_produce(store, campaign_name, limit, model, base_url):
    campaign = _campaign(store, campaign_name)
    summary = produce_batch(
        store, campaign, limit=max(0, limit), model=model, base_url=base_url
    )
    print(
        f"[outreach] {campaign_name}: "
        f"started={summary.get('started',0)} generated={summary.get('generated',0)}"
    )
    for message in summary.get("messages") or []:
        text = str(message.get("text") or "").replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "..."
        print(
            f"  {message.get('message_id')} v{message.get('version','?')} "
            f"[{message.get('status','?')}] {text}"
        )


def cmd_approve(store, message_id):
    result = approve(store, message_id)
    print(
        f"[outreach] approved {message_id} -> "
        f"prospect={result.get('prospect_id')} campaign={result.get('campaign_id')}"
    )
    print("[outreach] NEXT: manually send the approved message on LinkedIn.")


def cmd_reject(store, message_id, note):
    reject(store, message_id, note=note)
    print(f"[outreach] rejected {message_id}")


def cmd_mark_sent(store, campaign_name, prospect_id):
    campaign = _campaign(store, campaign_name)
    cp = store.get_campaign_prospect(campaign["campaign_id"], prospect_id)
    if not cp:
        raise SystemExit(
            f"prospect '{prospect_id}' is not assigned to campaign '{campaign_name}'"
        )
    mark_sent(store, cp)
    print(f"[outreach] marked sent: {prospect_id} / {campaign_name}")


def cmd_reply(store, campaign_name, prospect_id, text, model):
    campaign = _campaign(store, campaign_name)
    result = register_reply(store, campaign, prospect_id, text, model=model)
    classification = result.get("classification") or {}
    print(
        f"[outreach] {prospect_id} -> "
        f"intent={classification.get('intent','?')} "
        f"objection={classification.get('objection','?')} "
        f"commercial={classification.get('commercial_intent','?')}"
    )
    draft = result.get("objection_draft")
    if draft:
        print(f"[outreach] draft queued for human review: {draft.get('message_id')}")


def cmd_followups(store, campaign_name):
    campaign = _campaign(store, campaign_name)
    moved = make_follow_up_eligible(store, campaign)
    print(f"[outreach] {len(moved)} follow-up(s) now eligible")


def _all_funnels(store):
    return {
        campaign["campaign_id"]: funnel_for_campaign(store, campaign["campaign_id"])
        for campaign in store.campaigns()
    }


def cmd_report(store, campaign_name=None, as_json=False):
    if campaign_name:
        campaign = _campaign(store, campaign_name)
        data = overview(store, campaign["campaign_id"])
        data["funnel"] = funnel_for_campaign(store, campaign["campaign_id"])
    else:
        data = overview(store)
        data["funnels"] = _all_funnels(store)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return
    print(
        f"contacted={data.get('contacted',0)} "
        f"replied={data.get('replied',0)} "
        f"reply_rate={data.get('reply_rate',0)}% "
        f"visited={data.get('visited',0)} "
        f"activated={data.get('activated',0)} "
        f"customers={data.get('customer',0)}"
    )
    for strategy, stats in (data.get("by_strategy") or {}).items():
        print(
            f"  strategy {strategy}: contacted={stats.get('contacted',0)} "
            f"reply_rate={stats.get('reply_rate',0)}% "
            f"activation={stats.get('activation_rate',0)}%"
        )


def build_parser():
    ap = argparse.ArgumentParser(
        description="Phase 3 — deterministic/manual LinkedIn outreach engine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    actions = ap.add_mutually_exclusive_group()
    actions.add_argument("--sync", action="store_true",
                         help="sync config/campaigns/*.yaml into the database")
    actions.add_argument("--assign", action="store_true",
                         help="assign eligible prospects to a campaign")
    actions.add_argument("--produce", action="store_true",
                         help="generate validated message drafts")
    actions.add_argument("--approve", metavar="MESSAGE_ID",
                         help="approve one generated message")
    actions.add_argument("--reject", metavar="MESSAGE_ID",
                         help="reject one generated message")
    actions.add_argument("--mark-sent", action="store_true",
                         help="record that a human manually sent the message")
    actions.add_argument("--reply", action="store_true",
                         help="record and classify a manually pasted inbound reply")
    actions.add_argument("--followups", action="store_true",
                         help="make due follow-ups eligible")
    actions.add_argument("--queue", action="store_true",
                         help="show messages waiting for human review")
    actions.add_argument("--report", action="store_true",
                         help="print Phase 3 analytics")
    actions.add_argument("--auto", action="store_true",
                         help="sync + assign + produce drafts + report for all campaigns; NEVER sends")
    actions.add_argument("--approve-all", action="store_true",
                         help="approve all prospects currently in human review")
    actions.add_argument("--gui", action="store_true",
                         help="open the Phase 3 desktop control center")

    ap.add_argument("--campaign", default=None,
                    help="campaign name, e.g. ai_engineers")
    ap.add_argument("--top", type=int, default=5,
                    help="maximum prospects to assign; 0 = all")
    ap.add_argument("--limit", type=int, default=5,
                    help="maximum drafts to generate; 0 = all pending")
    ap.add_argument("--prospect", default=None)
    ap.add_argument("--text", default=None)
    ap.add_argument("--note", default=None)
    ap.add_argument("--model", default="gemma4:31b-cloud")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--json", dest="as_json", action="store_true")
    return ap


def _print_queue(store, campaign_name=None, as_json=False):
    items = approval_queue(store, _campaign(store, campaign_name)["campaign_id"]) if campaign_name else approval_queue(store)
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=2, default=str))
        return
    if not items:
        print("[outreach] approval queue is empty")
        return
    print(f"[outreach] {len(items)} message(s) awaiting human review")
    for item in items:
        print(
            f"  {item.get('message_id')} | "
            f"campaign={item.get('campaign_id')} | "
            f"prospect={item.get('prospect_id')} | "
            f"v{item.get('version')} | "
            f"{item.get('status')}"
        )


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    _dbg(f"main() args: {vars(args)}")

    if args.top < 0 or args.limit < 0:
        ap.error("--top and --limit cannot be negative")

    if args.gui or (argv is None and len(sys.argv) == 1):
        run_outreach_gui(model=args.model, base_url=args.url)
        return 0

    if args.assign and not args.campaign:
        ap.error("--assign requires --campaign")
    if args.produce and not args.campaign:
        ap.error("--produce requires --campaign")
    if args.mark_sent and (not args.campaign or not args.prospect):
        ap.error("--mark-sent requires --campaign and --prospect")
    if args.reply and (not args.campaign or not args.prospect or not args.text):
        ap.error("--reply requires --campaign, --prospect and --text")
    if args.followups and not args.campaign:
        ap.error("--followups requires --campaign")
    if args.reject and not args.note:
        args.note = "Rejected from Phase 3 review"

    store = Store(DB_PATH)
    try:
        if args.sync:
            created = sync_campaigns(store)
            if not created:
                print("[outreach] campaigns already synchronized; nothing new to create")
            for c in created:
                print(f"[outreach] {c['campaign_id']} ({c.get('name','')}, {c.get('strategy','')}) - {c.get('status','')}")
            return 0

        if args.approve_all:
            prospects = store.prospects(limit=None)
            ready = [p for p in prospects if p.get("status") == "ready_for_human_review"]
            if not ready:
                print("[outreach] no prospects in ready_for_human_review")
                return 0
            for p in ready:
                store.set_status(p["prospect_id"], "ready_for_outreach", next_action="READY_FOR_OUTREACH")
                store.record_feedback(p["prospect_id"], "approved", status_before="ready_for_human_review")
            print(f"[outreach] approved {len(ready)} prospect(s) to ready_for_outreach")
            return 0

        if args.auto:
            print("[outreach] AUTO MODE: drafts only. No LinkedIn messages are sent.")
            created = sync_campaigns(store)
            for c in created:
                print(f"[outreach] synced {c['campaign_id']} ({c.get('name','')})")
            for c in store.campaigns():
                cid = c["campaign_id"]
                candidates = eligible_for_campaign(
                    store, c, store.prospects(limit=None),
                    float((campaign_config(c) or {}).get("limits", {}).get("min_priority", 40))
                )
                if args.top > 0:
                    candidates = candidates[:args.top]
                assigned = assign_prospects(store, c, candidates) if candidates else []
                print(f"[outreach] {cid}: assigned={len(assigned)}")
            for c in store.campaigns():
                summary = produce_batch(store, c, limit=args.limit, model=args.model, base_url=args.url)
                print(f"[outreach] {c['campaign_id']}: generated={summary.get('generated',0)}")
            cmd_report(store, None, args.as_json)
            return 0

        if args.assign:
            cmd_assign(store, args.campaign, args.top)
        elif args.produce:
            cmd_produce(store, args.campaign, args.limit, args.model, args.url)
        elif args.approve:
            cmd_approve(store, args.approve)
        elif args.reject:
            cmd_reject(store, args.reject, args.note)
        elif args.mark_sent:
            cmd_mark_sent(store, args.campaign, args.prospect)
        elif args.reply:
            cmd_reply(store, args.campaign, args.prospect, args.text, args.model)
        elif args.followups:
            cmd_followups(store, args.campaign)
        elif args.queue:
            _print_queue(store, args.campaign, args.as_json)
        elif args.report:
            cmd_report(store, args.campaign, args.as_json)
        else:
            ap.print_help()
        return 0
    except Exception as e:
        _dbg(f"EXCEPTION phase3 main: {e}\n{traceback.format_exc()}")
        raise
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())