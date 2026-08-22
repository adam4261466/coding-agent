"""research_mode must be tracked and must separate offline vs live_browser evidence."""

import pytest

from linkedin_intelligence.research.qualifier import qualify, _rule_fallback, _sanitize
from linkedin_intelligence.store import Store

UNREACHABLE = "http://127.0.0.1:1"


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "test.db"))
    yield s
    s.close()


def _prospect():
    return {
        "prospect_id": "p1",
        "full_name": "Test Prospect",
        "raw_position": "Data Engineer",
        "current_company": "Acme",
        "total_score": 70,
        "commercial_intent": "unknown",
        "status": "new",
        "evidence": ["Role: Data Engineer", "Company: Acme"],
    }


def test_unreachable_llm_falls_back_to_rules_and_keeps_research_mode(store):
    q = qualify(store, _prospect(), base_url=UNREACHABLE, evidence=[],
                research_mode="offline", timeout=3)
    assert q["method"] == "rules"
    assert q["research_mode"] == "offline"
    assert q["confidence"] == 0.4
    assert q["problem_fit_score"] is None


def test_live_browser_mode_is_preserved_through_fallback(store):
    q = qualify(store, _prospect(), base_url=UNREACHABLE, evidence=[],
                research_mode="live_browser", timeout=3)
    assert q["research_mode"] == "live_browser"
    assert q["method"] == "rules"


def test_rule_fallback_never_claims_observed_evidence():
    q = _rule_fallback(_prospect())
    assert "No browser research performed" in q["uncertainties"]
    assert "Problem fit unknown" in q["uncertainties"]


def test_sanitize_keeps_unknown_as_valid_null():
    q = _sanitize({"fit_score": 60, "problem_fit_score": None,
                   "confidence": 0.7, "reason": "r",
                   "recommended_next_action": "RESEARCH_MORE"})
    assert q["problem_fit_score"] is None
    q2 = _sanitize({"fit_score": "bad", "confidence": "oops",
                    "recommended_next_action": "NONSENSE"})
    assert q2["fit_score"] == 50
    assert q2["confidence"] == 0.5
    assert q2["recommended_next_action"] == "RESEARCH_MORE"
