"""Evidence provenance: observed vs inference are stored and served separately."""

import pytest

from linkedin_intelligence.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "test.db"))
    yield s
    s.close()


def test_observed_evidence_requires_an_exact_observation(store):
    with pytest.raises(ValueError):
        store.add_evidence("p1", "Uses our stack", source_type="observed")


def test_observed_evidence_roundtrip(store):
    store.add_evidence("p1", "Mentions document-heavy workflow",
                       observation='Profile text: "we process 40k PDFs/day"',
                       source="linkedin_profile", source_type="observed",
                       confidence=0.9, collector="browser_agent",
                       research_task_id="research_abc")
    rows = store.observed_evidence_for("p1")
    assert len(rows) == 1
    assert rows[0]["source_type"] == "observed"
    assert rows[0]["observation"].startswith("Profile text:")
    assert rows[0]["research_task_id"] == "research_abc"
    assert rows[0]["collector"] == "browser_agent"
    assert rows[0]["evidence_id"].startswith("ev_")


def test_inference_is_never_served_as_observed(store):
    store.add_evidence("p1", "Plausibly needs document automation",
                       observation=None, source="llm_reasoning",
                       source_type="inference", confidence=0.5,
                       collector="llm_qualifier")
    assert store.observed_evidence_for("p1") == []
    assert len(store.evidence_for("p1")) == 1
    assert store.evidence_for("p1")[0]["source_type"] == "inference"


def test_add_inference_explicitly_stores_basis(store):
    store.add_inference("p1", "High buying intent given stack overlap",
                        evidence_basis=["ev_abc", "Profile mentions Spark"],
                        confidence=0.6)
    rows = store.evidence_for("p1")
    assert rows[0]["source_type"] == "inference"
    assert rows[0]["collector"] == "llm_qualifier"
    assert "ev_abc" in rows[0]["observation"]
