"""Evidence store: every claim carries full provenance.

Observed claims (browser/extractor) require an exact observation. Model
inferences are stored separately and can NEVER be recorded as observed facts.
"""


def record_findings(store, prospect_id: str, findings: list,
                    research_task_id: str = None):
    """Persist browser/research findings as OBSERVED evidence rows."""
    for f in findings:
        store.add_evidence(
            prospect_id=prospect_id,
            claim=f.get("claim", ""),
            observation=f.get("evidence") or f.get("observation"),
            source=f.get("source", "research"),
            source_type="observed",
            confidence=f.get("confidence"),
            collector=f.get("collector", "research_agent"),
            research_task_id=research_task_id or f.get("research_task_id"),
        )


def record_inference(store, prospect_id: str, claim: str,
                     evidence_basis: list = None, confidence: float = None,
                     collector: str = "llm_qualifier",
                     research_task_id: str = None):
    store.add_inference(prospect_id, claim, evidence_basis, confidence,
                        collector, research_task_id)


def evidence_block(store, prospect: dict) -> list:
    """Evidence for the qualifier: observed DB evidence + Phase 1 facts.

    Inferences are deliberately EXCLUDED from this block; the qualifier sees
    only things that were actually observed.
    """
    items = []
    for e in store.observed_evidence_for(prospect["prospect_id"]):
        items.append({
            "evidence_id": e["evidence_id"],
            "claim": e["claim"],
            "observation": e["observation"],
            "source": e["source"],
            "source_type": e["source_type"],
            "confidence": e.get("confidence"),
            "collector": e["collector"],
            "research_task_id": e.get("research_task_id"),
        })
    for fact in prospect.get("evidence", []):
        items.append({"claim": fact, "source": "linkedin export",
                      "source_type": "observed", "collector": "phase1",
                      "confidence": 1.0 if fact.startswith(("Role:", "Company:"))
                      else 0.8})
    return items


def offline_evidence(prospect: dict) -> list:
    """Phase 1 facts only, provenance-shaped (no browser research)."""
    items = []
    for fact in prospect.get("evidence", []):
        items.append({"claim": fact, "source": "linkedin export",
                      "source_type": "observed", "collector": "phase1",
                      "confidence": 0.9})
    return items
