"""Crash-resumable Phase 2 prospect processing.

IMPORTANT INTEGRATION POINTS
----------------------------
The functions below are intentionally small adapters around your existing
browser/LLM code. Replace only the three adapter functions:
    run_research_step
    qualify_prospect
    choose_next_action

The persistence and resume behavior should not be removed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from workflow_state import WorkflowStore
from linkedin_intelligence.research.agent import run_research as research_agent_run
from linkedin_intelligence.research.qualifier import qualify as qualify_agent_run
from linkedin_intelligence.research.next_action import assign_next_action as next_action_agent_run
from linkedin_intelligence.ingest.segmentation import _matches, load_segment_defs

DEFAULT_DB_PATH = Path(
    r"C:\Users\Admin\Desktop\coding-agent\linkedin_intelligence\db\linkedin_intelligence.sqlite"
)
# Keep the research steps deterministic and individually checkpointable. Replace
# or extend these keys with the real browser operations used by your agent.
RESEARCH_STEPS = [
    "open_profile",
    "profile_snapshot",
    "experience",
    "education",
    "projects",
    "skills",
    "company",
    "activity",
    "contact_or_website",
    "research_summary",
]


def run_research_step(prospect: dict[str, Any], step_key: str) -> dict[str, Any]:
    """ADAPTER: call your existing browser research for exactly one step.
 
    Return a dictionary. Any evidence items returned here are persisted before
    the next step begins, so a browser crash cannot erase earlier findings.
    """
    task = {
        "task_id": "step_" + step_key, 
        "objective": f"Research {step_key} for {prospect.get('full_name', 'prospect')}",
        "stage": "acquisition",
        "required_fields": ["relevant_evidence"],
        "budget": {"max_steps": 8, "max_snapshots": 6, "max_pages": 1, "max_evidence": 8},
    }
    # Map the step_key to a general research call. 
    # Since the existing agent.run handles a full prompt, we adapt it here.
    result = research_agent_run(task, prospect)
    return {"evidence": result.get("findings", []), "summary": result.get("summary")}


def qualify_prospect(prospect: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """ADAPTER: call your existing LLM qualification function.
 
    Expected output keys: fit_score, confidence, rationale, optional payload.
    """
    # We need a dummy store for the qualify_agent_run adapter
    class DummyStore:
        def evidence_block(self, s, p): return evidence
    
    result = qualify_agent_run(DummyStore(), prospect, evidence=evidence)
    return {
        "fit_score": result.get("fit_score"),
        "confidence": result.get("confidence"),
        "rationale": result.get("reason"),
        "payload": result
    }


def segment_prospect(prospect: dict[str, Any], qualification: dict[str, Any]) -> dict[str, Any]:
    """ADAPTER: return segment + optional confidence/payload."""
    defs = load_segment_defs()
    matched = [d["name"] for d in defs if _matches(prospect, d.get("rules", {}))]
    segment = matched[0] if matched else "unsegmented"
    return {"segment": segment, "confidence": 1.0 if matched else 0.0}


def choose_next_action(prospect: dict[str, Any], segment: dict[str, Any],
                        qualification: dict[str, Any]) -> dict[str, Any]:
    """ADAPTER: return next_action + optional payload."""
    action = next_action_agent_run(prospect, qualification)
    return {"next_action": action}


def load_prospect(conn, prospect_id: str) -> dict[str, Any] | None:
    """Load one prospect without assuming a specific original schema."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(prospects)").fetchall()}
    id_col = "prospect_id" if "prospect_id" in columns else "id"
    row = conn.execute(f"SELECT * FROM prospects WHERE {id_col}=?", (prospect_id,)).fetchone()
    return dict(row) if row else None


def update_prospect(conn, prospect_id: str, **fields: Any) -> None:
    """Update only columns that exist in the current prospects table.
 
    This allows the new workflow state to coexist with older versions of your
    prospect schema. Every call commits immediately.
    """
    available = {row[1] for row in conn.execute("PRAGMA table_info(prospects)").fetchall()}
    id_col = "prospect_id" if "prospect_id" in available else "id"
    usable = {k: v for k, v in fields.items() if k in available}
    if not usable:
        return
    assignments = ", ".join(f"{name}=?" for name in usable)
    values = list(usable.values()) + [prospect_id]
    conn.execute(f"UPDATE prospects SET {assignments} WHERE {id_col}=?", values)
    conn.commit()


def persist_step_output(store: WorkflowStore, prospect_id: str, result: dict[str, Any]) -> None:
    """Persist every evidence item returned by a research step immediately."""
    evidence_items = result.get("evidence", []) or []
    if isinstance(evidence_items, dict):
        evidence_items = [evidence_items]

    for item in evidence_items:
        if isinstance(item, str):
            item = {"evidence": item, "source": "browser"}
        evidence = str(item.get("evidence", "")).strip()
        if not evidence:
            continue
        store.save_evidence(
            prospect_id=prospect_id,
            source=str(item.get("source") or "browser"),
            evidence=evidence,
            url=item.get("url"),
            source_type=item.get("source_type"),
            metadata=item.get("metadata"),
        )


def research_prospect(store: WorkflowStore, prospect: dict[str, Any]) -> None:
    prospect_id = str(prospect.get("prospect_id") or prospect.get("id"))
    task = store.ensure_task(prospect_id, "research", len(RESEARCH_STEPS), RESEARCH_STEPS)
    task_id = str(task["task_id"])

    if task["status"] == "completed":
        update_prospect(store.conn, prospect_id,
                        status="research",
                        current_stage="qualification")
        return

    update_prospect(store.conn, prospect_id,
                    status="research",
                    current_stage="research")
    store.start_task(prospect_id, "research", task_id)

    for index, step_key in enumerate(RESEARCH_STEPS, start=1):
        row = store.get_step(task_id, step_key)
        if row and row["status"] == "completed":
            continue

        try:
            store.begin_step(task_id, prospect_id, "research", index, step_key, "browser")
            result = run_research_step(prospect, step_key)
            if result is None:
                result = {}
            if not isinstance(result, dict):
                result = {"result": result}

            # Critical order: evidence first, then checkpoint completion.
            persist_step_output(store, prospect_id, result)
            store.complete_step(task_id, prospect_id, "research", index, step_key,
                                result=result, tool="browser")
        except Exception as exc:
            store.fail_step(task_id, prospect_id, "research", step_key, exc)
            raise

    store.finish_task(task_id, prospect_id, "research")
    update_prospect(store.conn, prospect_id,
                    status="ready_for_qualification",
                    current_stage="qualification",
                    research_mode="live_browser")


def get_all_evidence(store: WorkflowStore, prospect_id: str) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        "SELECT source, evidence, url, source_type, captured_at, metadata_json "
        "FROM prospect_evidence WHERE prospect_id=? ORDER BY captured_at ASC",
        (prospect_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if item.get("metadata_json"):
            try:
                item["metadata"] = json.loads(item.pop("metadata_json"))
            except json.JSONDecodeError:
                item["metadata"] = item.pop("metadata_json")
        result.append(item)
    return result


def qualification_prospect(store: WorkflowStore, prospect: dict[str, Any]) -> None:
    prospect_id = str(prospect.get("prospect_id") or prospect.get("id"))
    task = store.ensure_task(prospect_id, "qualification", 1, ["llm_qualification"])
    task_id = str(task["task_id"])

    if task["status"] == "completed":
        return

    update_prospect(store.conn, prospect_id, current_stage="qualification",
                    status="qualifying")
    store.start_task(prospect_id, "qualification", task_id)

    step = store.get_step(task_id, "llm_qualification")
    if not step or step["status"] != "completed":
        try:
            store.begin_step(task_id, prospect_id, "qualification", 1,
                             "llm_qualification", "llm")
            evidence = get_all_evidence(store, prospect_id)
            result = qualify_prospect(prospect, evidence)
            result = result or {}
            store.save_qualification(
                prospect_id=prospect_id,
                fit_score=result.get("fit_score"),
                confidence=result.get("confidence"),
                method=str(result.get("method") or "llm"),
                rationale=result.get("rationale"),
                payload=result.get("payload", result),
            )
            # Save the core values to prospects too when those columns exist.
            update_prospect(
                store.conn,
                prospect_id,
                qualification_fit=result.get("fit_score"),
                qualification_confidence=result.get("confidence"),
                qualification_method=str(result.get("method") or "llm"),
                total_score=result.get("total_score"),
            )
            store.complete_step(task_id, prospect_id, "qualification", 1,
                                "llm_qualification", result=result, tool="llm")
        except Exception as exc:
            store.fail_step(task_id, prospect_id, "qualification",
                            "llm_qualification", exc)
            raise

    store.finish_task(task_id, prospect_id, "qualification")
    update_prospect(store.conn, prospect_id, status="ready_for_segmentation",
                    current_stage="segmentation")


def segmentation_prospect(store: WorkflowStore, prospect: dict[str, Any]) -> None:
    prospect_id = str(prospect.get("prospect_id") or prospect.get("id"))
    task = store.ensure_task(prospect_id, "segmentation", 1, ["assign_segment"])
    task_id = str(task["task_id"])
    if task["status"] == "completed":
        return

    update_prospect(store.conn, prospect_id, current_stage="segmentation",
                    status="segmenting")
    store.start_task(prospect_id, "segmentation", task_id)

    step = store.get_step(task_id, "assign_segment")
    if not step or step["status"] != "completed":
        try:
            store.begin_step(task_id, prospect_id, "segmentation", 1,
                             "assign_segment", "llm")
            qual = store.conn.execute(
                "SELECT * FROM prospect_qualifications WHERE prospect_id=?",
                (prospect_id,),
            ).fetchone()
            qualification = dict(qual) if qual else {}
            result = segment_prospect(prospect, qualification) or {}
            store.save_segment(
                prospect_id, str(result.get("segment") or "unclassified"),
                result.get("confidence"), result.get("payload", result),
            )
            update_prospect(store.conn, prospect_id,
                            segment=result.get("segment"),
                            segment_confidence=result.get("confidence"))
            store.complete_step(task_id, prospect_id, "segmentation", 1,
                                "assign_segment", result=result, tool="llm")
        except Exception as exc:
            store.fail_step(task_id, prospect_id, "segmentation", "assign_segment", exc)
            raise

    store.finish_task(task_id, prospect_id, "segmentation")
    update_prospect(store.conn, prospect_id, status="ready_for_next_action",
                    current_stage="next_action")


def next_action_prospect(store: WorkflowStore, prospect: dict[str, Any]) -> None:
    prospect_id = str(prospect.get("prospect_id") or prospect.get("id"))
    task = store.ensure_task(prospect_id, "next_action", 1, ["choose_next_action"])
    task_id = str(task["task_id"])
    if task["status"] == "completed":
        return

    update_prospect(store.conn, prospect_id, current_stage="next_action",
                    status="choosing_next_action")
    store.start_task(prospect_id, "next_action", task_id)

    step = store.get_step(task_id, "choose_next_action")
    if not step or step["status"] != "completed":
        try:
            store.begin_step(task_id, prospect_id, "next_action", 1,
                             "choose_next_action", "llm")
            qual = store.conn.execute(
                "SELECT * FROM prospect_qualifications WHERE prospect_id=?",
                (prospect_id,),
            ).fetchone()
            seg = store.conn.execute(
                "SELECT * FROM prospect_segments WHERE prospect_id=?",
                (prospect_id,),
            ).fetchone()
            result = choose_next_action(prospect, dict(seg) if seg else {},
                                        dict(qual) if qual else {}) or {}
            action = str(result.get("next_action") or result.get("action") or "human_review")
            store.save_next_action(prospect_id, action, result.get("payload", result))
            update_prospect(store.conn, prospect_id, next_action=action,
                            status="ready_for_outreach", current_stage="complete")
            store.complete_step(task_id, prospect_id, "next_action", 1,
                                "choose_next_action", result=result, tool="llm")
        except Exception as exc:
            store.fail_step(task_id, prospect_id, "next_action", "choose_next_action", exc)
            raise

    store.finish_task(task_id, prospect_id, "next_action")
    update_prospect(store.conn, prospect_id,
                    status="ready_for_outreach", current_stage="complete")


def process_prospect(store: WorkflowStore, prospect_id: str) -> None:
    prospect = load_prospect(store.conn, prospect_id)
    if not prospect:
        raise ValueError(f"Unknown prospect_id: {prospect_id}")

    # Re-fetch after each stage so updates made by the previous stage are visible.
    research_prospect(store, prospect)
    prospect = load_prospect(store.conn, prospect_id) or prospect
    qualification_prospect(store, prospect)
    prospect = load_prospect(store.conn, prospect_id) or prospect
    segmentation_prospect(store, prospect)
    prospect = load_prospect(store.conn, prospect_id) or prospect
    next_action_prospect(store, prospect)


def pending_prospect_ids(store: WorkflowStore) -> list[str]:
    """Return prospects that have not reached a completed outreach-ready state."""
    columns = {row[1] for row in store.conn.execute("PRAGMA table_info(prospects)").fetchall()}
    id_col = "prospect_id" if "prospect_id" in columns else "id"
    if "status" in columns:
        rows = store.conn.execute(
            f"SELECT {id_col} FROM prospects "
            "WHERE status IS NULL OR status NOT IN ('ready_for_outreach', 'completed')"
        ).fetchall()
    else:
        rows = store.conn.execute(f"SELECT {id_col} FROM prospects").fetchall()
    return [str(row[0]) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Crash-resumable Phase 2")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--prospect-id", action="append")
    args = parser.parse_args()

    db_path = DEFAULT_DB_PATH

    print("=" * 70)
    print("PHASE 2 STARTING")
    print("=" * 70)
    print(f"Database: {db_path.resolve()}")
    print(f"Exists:   {db_path.exists()}")
    print("=" * 70)

    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found: {db_path}"
        )

    store = WorkflowStore(db_path)

    try:
        tables = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        if "prospects" not in tables:
            raise RuntimeError(
                "WRONG DATABASE OPENED.\n"
                f"Path: {db_path.resolve()}\n"
                f"Tables: {sorted(tables)}"
            )

        prospect_ids = args.prospect_id or pending_prospect_ids(store)

        print(f"Prospects to process: {len(prospect_ids)}")

        for prospect_id in prospect_ids:
            process_prospect(store, prospect_id)

    finally:
        store.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())