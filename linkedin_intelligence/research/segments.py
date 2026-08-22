"""Dynamic segmentation after research: role x band x buying evidence clusters."""

import os

from ..utils import INTELLIGENCE_DIR, save_json


def segment(prospects, store=None) -> list:
    """Group researched prospects into interpretable segments (deterministic)."""
    groups = {}
    for p in prospects:
        role = p.get("role_category") or "unknown"
        band = p.get("segment_band") or "low_relevance"
        key = f"{role}::{band}"
        groups.setdefault(key, []).append(p)

    segments = []
    for key, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        role, band = key.split("::")
        signals = [m.get("commercial_intent") for m in members]
        n_signal = sum(1 for s in signals if s in ("explicit", "possible"))
        avg = round(sum(m.get("total_score", 0) for m in members) / max(1, len(members)), 1)
        desc = (f"{role.replace('_', ' ').title()} prospects in the "
                f"{band.replace('_', ' ')} band; {n_signal} show a buying signal; "
                f"average score {avg}.")
        segments.append({
            "name": f"{role}::{band}",
            "role_category": role,
            "band": band,
            "description": desc,
            "size": len(members),
            "average_score": avg,
            "buying_signals": n_signal,
            "prospects": [m["prospect_id"] for m in members],
        })
    save_json(segments, os.path.join(INTELLIGENCE_DIR, "segments_research.json"))
    return segments
