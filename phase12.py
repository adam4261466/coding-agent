"""LinkedIn Intelligence — Phase 1 + Phase 2, one click.

Run this file (double-click it, or `python phase12.py`) and a small GUI
opens. Click "Run pipeline" and it runs Phase 1 (offline ingestion) and
then Phase 2 (research + qualification + segmentation) back to back.

The only choices exposed are the Phase 2 filter:
    - Batch type      exploitation / exploration
    - Min fit score   only process prospects at/above this score (0 = no filter)
    - Top N           max number of prospects to process (blank = all)

Everything else (model, Ollama URL, timeouts, offline/no-llm switches,
calibration) uses sane defaults baked into the script below — edit the
DEFAULTS dict near the top if you need to change one permanently.
"""

import contextlib
import json
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Console encoding
# ---------------------------------------------------------------------------

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, Exception):
    pass


# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------

from linkedin_intelligence.ingest import run_phase1
from linkedin_intelligence.ingest.segmentation import backfill_segmentation
from linkedin_intelligence.ingest.scorer import recompute_total
from linkedin_intelligence.utils import DEFAULT_SOURCE_DIR, DB_PATH, load_icp
from linkedin_intelligence.store import Store

from linkedin_intelligence.research import (
    build_research_tasks,
    qualify,
    segment,
    assign_next_action,
)
from linkedin_intelligence.research.evidence import offline_evidence, evidence_block
from linkedin_intelligence.research.agent import run_research
from linkedin_intelligence.research.pain_state import pain_state

from linkedin_intelligence.report import write_final_report
from linkedin_intelligence.calibration import write_calibration_report


# ---------------------------------------------------------------------------
# Fixed defaults — everything the old CLI let you tweak but that we no
# longer expose in the GUI. Edit here if you need a different value.
# ---------------------------------------------------------------------------

DEFAULTS = {
    "source_dir": DEFAULT_SOURCE_DIR,
    "model": "gemma4:31b-cloud",
    "url": "http://localhost:11434",
    "timeout": 600,
    "offline": False,
    "no_llm": False,
    "calibrate": False,
}

STATUS_MAP = {
    "SKIP": "skipped",
    "RESEARCH_MORE": "research",
    "READY_FOR_HUMAN_REVIEW": "ready_for_human_review",
    "READY_FOR_OUTREACH": "ready_for_outreach",
    "FOLLOW_UP": "follow_up",
    "DO_NOT_CONTACT": "do_not_contact",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def print_header(title):
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def print_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# PHASE 1
# ---------------------------------------------------------------------------

def run_phase1_step(source_dir):
    print_header("PHASE 1 — OFFLINE PROSPECT INTELLIGENCE")
    print(f"[phase1] source directory: {source_dir}")
    print("[phase1] starting ingestion...")

    summary = run_phase1(source_dir=source_dir, verbose=True)

    print()
    print("=" * 52)
    print("PHASE 1 SUMMARY")
    print_json(summary)

    return summary


# ---------------------------------------------------------------------------
# PHASE 2
# ---------------------------------------------------------------------------


# Field names to try first when looking up a prospect's company/school —
# used only to produce a clean "excluded company: X" message. The real
# matching also falls back to scanning the whole record (see below), since
# Phase 1 data doesn't reliably put company/school under one fixed key.


def run_phase2_step(
    batch_type="exploitation",
    top=None,
    min_score=None,
    model=DEFAULTS["model"],
    url=DEFAULTS["url"],
    timeout=DEFAULTS["timeout"],
    offline=DEFAULTS["offline"],
    no_llm=DEFAULTS["no_llm"],
    calibrate=DEFAULTS["calibrate"],
):
    """Run Phase 2, restricted to prospects matching the filter."""

    print_header("PHASE 2 — RESEARCH + QUALIFICATION + SEGMENTATION")
    print(
        f"[phase2] filter: batch_type={batch_type} "
        f"min_score={min_score if min_score else 'none'} "
        f"top={top if top else 'all'}"
    )

    store = Store(DB_PATH)

    try:
        # 1. Backfill segmentation/introspection data
        n = backfill_segmentation(store)
        if n:
            print(f"[phase2] segmentation introspection backfilled for {n} prospects")

        # 2. Load ICP configuration
        icp = load_icp()
        min_conf = float(icp.get("qualification", {}).get("min_confidence_for_ready", 0.5))

        # 3. Deterministic pain state baseline
        all_prospects = store.prospects()
        for prospect in all_prospects:
            prospect["pain_state"] = pain_state(prospect)
        if all_prospects:
            store.upsert_prospects(all_prospects)

        # 4. Build research tasks (batch type + top N handled here)
        tasks = build_research_tasks(
            store, limit=top, batch_type=batch_type,
            min_score=min_score,
        )
        print(f"[phase2] {len(tasks)} research tasks created (batch={batch_type})")

        if not tasks:
            print("[phase2] no research tasks available.")
            return {"qualified": 0, "tasks": 0, "segments": 0, "batch_type": batch_type}

        # 5. Process tasks
        results = []

        for i, task in enumerate(tasks, 1):
            prospect = store.get_prospect(task["prospect_id"])

            if not prospect:
                print(f"[phase2] [{i}/{len(tasks)}] prospect missing -> SKIP")
                store.set_task_status(task["task_id"], "skipped")
                continue

            name = prospect.get("full_name") or prospect["prospect_id"]

            # Skip already-qualified prospects
            existing_quals = [
                q for q in store.qualifications()
                if q["prospect_id"] == prospect["prospect_id"] and q.get("fit_score") is not None
            ]
            if existing_quals:
                print(f"[phase2] [{i}/{len(tasks)}] {name} -> SKIP (already qualified)")
                store.set_task_status(task["task_id"], "skipped")
                continue

            print(f"[phase2] [{i}/{len(tasks)}] {name}")

            # Research mode
            research_mode = "offline"

            if not offline:
                try:
                    research_result = run_research(
                        task, prospect, model=model, base_url=url, store=store,
                    )
                except Exception as exc:
                    research_result = {"findings": [], "error": str(exc)}
                    print(f"         browser research failed: {exc}")

                findings = research_result.get("findings") or []
                if findings:
                    research_mode = "live_browser"
                    print(f"         evidence: {len(findings)} items (mode: {research_mode})")
                else:
                    print("         no browser evidence captured -> offline evidence only")
            else:
                print("         offline evidence (Phase 1 facts only)")

            # Evidence available to qualification
            if research_mode == "live_browser":
                evidence = evidence_block(store, prospect)
            else:
                evidence = offline_evidence(prospect)

            # Qualification
            if no_llm:
                q = {
                    "fit_score": int(round(prospect.get("total_score", 0))),
                    "problem_fit_score": None,
                    "confidence": 0.4,
                    "reason": "Rule fallback (--no-llm).",
                    "evidence_used": prospect.get("evidence", [])[:5],
                    "uncertainties": ["No LLM qualification"],
                    "recommended_next_action": "RESEARCH_MORE",
                    "contradictions": [],
                    "model": "rules",
                    "method": "rules",
                    "research_mode": research_mode,
                }
            else:
                try:
                    q = qualify(
                        store, prospect, model=model, base_url=url,
                        evidence=evidence, research_mode=research_mode, timeout=timeout,
                    )
                except Exception as exc:
                    print(f"         qualification failed: {exc}")
                    q = {
                        "fit_score": int(round(prospect.get("total_score", 0))),
                        "problem_fit_score": None,
                        "confidence": 0.0,
                        "reason": f"Qualification error: {exc}",
                        "evidence_used": prospect.get("evidence", [])[:5],
                        "uncertainties": ["LLM qualification failed"],
                        "recommended_next_action": "RESEARCH_MORE",
                        "contradictions": [],
                        "model": model,
                        "method": "error_fallback",
                        "research_mode": research_mode,
                    }

            # Save qualification
            q["prospect_id"] = prospect["prospect_id"]
            q["research_stage"] = task.get("stage", "acquisition")
            q["created_at"] = datetime.now(timezone.utc).isoformat()
            store.save_qualification(q)

            # Fold qualification into prospect
            prospect["problem_fit_score"] = q.get("problem_fit_score")
            prospect["problem_fit_confidence"] = (
                round(q.get("confidence", 0.0), 2)
                if q.get("problem_fit_score") is not None else None
            )
            prospect["research_mode"] = research_mode
            prospect["qualification_fit"] = q.get("fit_score")
            prospect["qualification_confidence"] = q.get("confidence")
            prospect["contradictions"] = q.get("contradictions", [])

            # Recompute total score when problem-fit exists
            if prospect.get("problem_fit_score") is not None:
                recompute_total(prospect, icp)

                high_threshold = icp.get("high_relevance_threshold", 55)
                potential_threshold = icp.get("potential_relevance_threshold", 40)

                if prospect["total_score"] >= high_threshold:
                    prospect["segment_band"] = "high_relevance"
                elif prospect["total_score"] >= potential_threshold:
                    prospect["segment_band"] = "potential_relevance"
                else:
                    prospect["segment_band"] = "low_relevance"

            # Pain state after qualification
            prospect["pain_state"] = pain_state(prospect, q)
            store.upsert_prospects([prospect])

            # Determine next action
            action = assign_next_action(prospect, q, min_confidence_for_ready=min_conf)
            next_status = STATUS_MAP.get(action, "research")
            store.set_status(prospect["prospect_id"], next_status, next_action=action)

            # Complete task
            store.set_task_status(task["task_id"], "done")

            results.append({
                "name": name,
                "fit_score": q.get("fit_score"),
                "problem_fit": q.get("problem_fit_score"),
                "confidence": q.get("confidence"),
                "mode": research_mode,
                "stage": task.get("stage", "acquisition"),
                "contradictions": len(q.get("contradictions", [])),
                "action": action,
            })

        # 6. Segment researched prospects
        reviewed = [
            p for p in store.prospects()
            if p.get("status") in (
                "ready_for_human_review", "ready_for_outreach", "follow_up", "research",
            )
        ]
        segs = segment(reviewed, store)
        for s in segs:
            store.save_segment(s["name"], s["description"], s["prospects"])

        # 7. Print summary
        print()
        print("=" * 52)
        print(f"PHASE 2 SUMMARY: {len(results)} prospects qualified (batch={batch_type})")

        sortable_results = [r for r in results if r.get("fit_score") is not None]
        for result in sorted(sortable_results, key=lambda x: -x["fit_score"])[:20]:
            pf = result["problem_fit"] if result["problem_fit"] is not None else "-"
            confidence = result.get("confidence")
            confidence_text = "-" if confidence is None else f"{confidence:.2f}"
            print(
                f"  fit={result['fit_score']:>3}  pf={pf:>3}  conf={confidence_text:>4}  "
                f"mode={result['mode']:<12} stage={result['stage'][:4]:<4} "
                f"flag={result['contradictions']:<2} {result['action']:<22} {result['name']}"
            )

        print(
            f"\nSegments: {len(segs)} -> "
            "linkedin_intelligence/data/intelligence/segments_research.json"
        )

        # 8. Final report
        report = write_final_report(store, top_n=top)
        print(f"\nFinal report: {report}")

        # 9. Optional calibration
        calibration_report = None
        if calibrate:
            calibration_report = write_calibration_report(store)
            print(f"Calibration report: {calibration_report}")

        return {
            "qualified": len(results),
            "tasks": len(tasks),
            "segments": len(segs),
            "batch_type": batch_type,
            "results": results,
            "report": report,
            "calibration_report": calibration_report,
        }

    finally:
        store.close()


# ---------------------------------------------------------------------------
# Full pipeline (Phase 1 -> Phase 2, filtered)
# ---------------------------------------------------------------------------

def run_full_pipeline(
    source_dir, batch_type, top, min_score,
):
    print_header("LINKEDIN INTELLIGENCE — PHASE 1 + PHASE 2")
    print("[pipeline] Phase 1 will run first.")

    phase1_summary = run_phase1_step(source_dir)

    print()
    print("[pipeline] Phase 1 complete.")
    print("[pipeline] Starting Phase 2...")

    phase2_summary = run_phase2_step(
        batch_type=batch_type,
        top=top,
        min_score=min_score,
    )

    print_header("PHASE 1 + PHASE 2 COMPLETE")
    print("Phase 1 completed successfully.")
    print(f"Phase 2 qualified: {phase2_summary.get('qualified', 0)} prospects")
    print(f"Phase 2 segments: {phase2_summary.get('segments', 0)}")

    return {"phase1": phase1_summary, "phase2": phase2_summary}


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class QueueWriter:
    """File-like object that pushes writes into a queue for the GUI thread."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)

    def flush(self):
        pass


class PipelineGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("LinkedIn Intelligence Pipeline")
        self.root.geometry("760x560")

        self.output_queue = queue.Queue()
        self.worker_thread = None

        self._build_widgets()
        self._poll_queue()

    def _build_widgets(self):
        pad = {"padx": 10, "pady": 6}

        filter_frame = ttk.LabelFrame(self.root, text="Phase 2 filter")
        filter_frame.pack(fill="x", **pad)

        # Batch type
        ttk.Label(filter_frame, text="Batch type:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.batch_type_var = tk.StringVar(value="exploitation")
        batch_combo = ttk.Combobox(
            filter_frame, textvariable=self.batch_type_var,
            values=["exploitation", "exploration"], state="readonly", width=16,
        )
        batch_combo.grid(row=0, column=1, sticky="w", padx=8, pady=6)

        # Min score
        ttk.Label(filter_frame, text="Min fit score:").grid(row=0, column=2, sticky="w", padx=8, pady=6)
        self.min_score_var = tk.StringVar(value="0")
        ttk.Entry(filter_frame, textvariable=self.min_score_var, width=8).grid(
            row=0, column=3, sticky="w", padx=8, pady=6
        )

        # Top N
        ttk.Label(filter_frame, text="Top N (blank = all):").grid(row=0, column=4, sticky="w", padx=8, pady=6)
        self.top_n_var = tk.StringVar(value="")
        ttk.Entry(filter_frame, textvariable=self.top_n_var, width=8).grid(
            row=0, column=5, sticky="w", padx=8, pady=6
        )

        # Run button + status
        run_frame = ttk.Frame(self.root)
        run_frame.pack(fill="x", **pad)

        self.run_button = ttk.Button(run_frame, text="Run pipeline", command=self.on_run)
        self.run_button.pack(side="left")

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(run_frame, textvariable=self.status_var).pack(side="left", padx=12)

        # Output log
        log_frame = ttk.LabelFrame(self.root, text="Output")
        log_frame.pack(fill="both", expand=True, **pad)

        self.log_widget = scrolledtext.ScrolledText(log_frame, wrap="word", state="disabled")
        self.log_widget.pack(fill="both", expand=True, padx=4, pady=4)

    def _append_log(self, text):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", text)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                item = self.output_queue.get_nowait()
                if item is None:
                    self._on_worker_done()
                else:
                    self._append_log(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_worker_done(self):
        self.status_var.set("Idle")
        self.run_button.configure(state="normal")

    def on_run(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        # Validate filter inputs
        try:
            min_score = float(self.min_score_var.get() or 0)
        except ValueError:
            messagebox.showerror("Invalid input", "Min fit score must be a number.")
            return

        top_raw = self.top_n_var.get().strip()
        top = None
        if top_raw:
            try:
                top = int(top_raw)
            except ValueError:
                messagebox.showerror("Invalid input", "Top N must be a whole number.")
                return

        batch_type = self.batch_type_var.get()

        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

        self.status_var.set("Running...")
        self.run_button.configure(state="disabled")

        self.worker_thread = threading.Thread(
            target=self._worker,
            args=(batch_type, top, min_score),
            daemon=True,
        )
        self.worker_thread.start()

    def _worker(self, batch_type, top, min_score):
        writer = QueueWriter(self.output_queue)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            try:
                run_full_pipeline(
                    source_dir=DEFAULTS["source_dir"],
                    batch_type=batch_type,
                    top=top,
                    min_score=min_score,
                )
            except Exception as exc:
                print(f"\n[ERROR] Pipeline failed: {exc}\n")
        self.output_queue.put(None)


def main():
    root = tk.Tk()
    PipelineGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()