"""Phase 1 ingestion pipeline (offline, no LLM, no browser)."""

from .pipeline import run_phase1
from .scanner import scan
from .deduplicator import deduplicate
from .scorer import score

__all__ = ["run_phase1", "scan", "deduplicate", "score"]
