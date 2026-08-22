"""run_phase1(): orchestrate the full offline ingestion pipeline.

Pipeline order:
  raw export -> import scanner -> parse -> normalize
  -> taxonomy -> deduplicate -> relationship/conversation db -> hard exclusions
  -> ICP scoring -> SQLite.
"""

import os

from ..utils import (ensure_dirs, load_icp, load_taxonomy, load_privacy,
                     own_identity, DEFAULT_SOURCE_DIR)
from ..store import Store
from .scanner import scan
from .parsers import (parse_connections, parse_messages, parse_invitations,
                      parse_education, parse_company_follows, parse_profile)
from .normalizer import normalize_company, normalize_position
from .classifier import classify_position, classify_company
from .deduplicator import deduplicate
from .relationship import build_conversations
from .scorer import score, build_prospect


def run_phase1(source_dir: str = None, db_path: str = None,
               verbose: bool = True) -> dict:
    source_dir = source_dir or DEFAULT_SOURCE_DIR
    ensure_dirs()

    if not os.path.isdir(source_dir):
        raise FileNotFoundError(f"LinkedIn export not found: {source_dir}")

    if verbose:
        print(f"[phase1] scanning export: {source_dir}")

    # 1. discover the schema, don't assume it
    report = scan(source_dir)
    if report.get("error"):
        raise FileNotFoundError(report["error"])

    # 2. parse
    connections = parse_connections(os.path.join(source_dir, "Connections.csv"))
    messages = parse_messages(source_dir)
    invitations = parse_invitations(os.path.join(source_dir, "Invitations.csv"))
    education = parse_education(os.path.join(source_dir, "Education.csv"))
    profile = parse_profile(os.path.join(source_dir, "Profile.csv"))
    follows = parse_company_follows(os.path.join(source_dir, "Company Follows.csv"))

    identity = own_identity(profile)
    if verbose:
        print(f"[phase1] identity: {identity['full_name'] or 'unknown'}, "
              f"{len(connections)} raw connections, {len(messages)} messages, "
              f"{len(invitations)} invitations")

    # 4. conversations (code, no LLM)
    conversations = build_conversations(messages, identity["full_name"])

    # 5. normalize + classify + deduplicate
    config = {
        "icp": load_icp(),
        "taxonomy": {
            "classify_position": classify_position,
            "classify_company": classify_company,
            "tax": load_taxonomy(),
        },
        "privacy": load_privacy(),
    }

    unique, merge_log, dedup_report = deduplicate(connections)
    if verbose:
        print(f"[phase1] dedup: {dedup_report['raw_records']} -> "
              f"{dedup_report['unique_prospects']} unique")

    # 6. build prospects
    prospects = [build_prospect(rec, conversations, invitations, config,
                                my_schools=[e.school for e in education])
                 for rec in unique]

    # 7. hard exclusions + scoring
    kept, excluded = score(prospects, config)
    if verbose:
        print(f"[phase1] exclusions: {len(excluded)}, scored: {len(kept)}")

    # 7b. segmentation (before Phase 2 research)
    from .segmentation import assign_segments
    assign_segments(kept)
    if verbose:
        seg_counts = {}
        for p in kept:
            for s in p["segments"]:
                seg_counts[s] = seg_counts.get(s, 0) + 1
        print(f"[phase1] segments: {dict(sorted(seg_counts.items(), key=lambda kv: -kv[1])[:6])}")

    # 8. persist
    store = Store(db_path)
    try:
        store.reset()
        store.upsert_prospects(kept)
        store.save_exclusions(excluded)
        linked_convos = []
        for p in kept:
            for c in conversations:
                if c["conversation_id"] in p.get("conversation_ids", []):
                    row = dict(c)
                    row["prospect_id"] = p["prospect_id"]
                    linked_convos.append(row)
        store.save_conversations(linked_convos)
        store.set_meta("raw_connections", str(dedup_report["raw_records"]))
        summary = summarize(store, kept, excluded, dedup_report, report,
                            follows)
    finally:
        store.close()

    if verbose:
        print(f"[phase1] done: {summary['unique_prospects']} prospects in DB")
    return summary


def summarize(store, kept, excluded, dedup_report, scan_report,
              follows) -> dict:
    bands = {}
    for p in kept:
        bands[p["segment_band"]] = bands.get(p["segment_band"], 0) + 1
    return {
        "raw_connections": dedup_report["raw_records"],
        "unique_prospects": dedup_report["unique_prospects"],
        "excluded": len(excluded),
        "scored_prospects": len(kept),
        "bands": bands,
        "db_stats": store.stats(),
    }


if __name__ == "__main__":
    run_phase1()
