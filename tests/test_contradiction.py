"""Contradiction validation: the model's numbers must agree with its action."""

from linkedin_intelligence.research.qualifier import validate_qualification
from linkedin_intelligence.utils import load_icp

ICP = load_icp()


def _q(fit=80, pfs=70, conf=0.9, action="READY_FOR_HUMAN_REVIEW"):
    return {"fit_score": fit, "problem_fit_score": pfs, "confidence": conf,
            "recommended_next_action": action}


def test_low_fit_approval_is_downgraded_and_flagged():
    q = validate_qualification({"commercial_intent": "unknown"},
                               _q(fit=20, action="READY_FOR_HUMAN_REVIEW"), ICP)
    assert q["recommended_next_action"] == "RESEARCH_MORE"
    assert any("fit 20" in c for c in q["contradictions"])


def test_approval_without_problem_fit_is_allowed():
    q = validate_qualification({"commercial_intent": "unknown"},
                               _q(pfs=None), ICP)
    assert q["recommended_next_action"] == "READY_FOR_HUMAN_REVIEW"


def test_low_confidence_approval_is_downgraded():
    q = validate_qualification({"commercial_intent": "unknown"},
                               _q(conf=0.2), ICP)
    assert q["recommended_next_action"] == "RESEARCH_MORE"


def test_high_fit_skip_is_flagged_but_not_overridden():
    q = validate_qualification({"commercial_intent": "unknown"},
                               _q(fit=85, pfs=None, action="SKIP"), ICP)
    assert q["recommended_next_action"] == "SKIP"
    assert any("SKIP" in c for c in q["contradictions"])


def test_dnc_without_declined_signal_is_flagged():
    q = validate_qualification({"commercial_intent": "unknown"},
                               _q(fit=50, pfs=50, action="DO_NOT_CONTACT"), ICP)
    assert q["recommended_next_action"] == "DO_NOT_CONTACT"
    assert any("DO_NOT_CONTACT" in c for c in q["contradictions"])


def test_consistent_qualification_passes_clean():
    q = validate_qualification({"commercial_intent": "unknown"}, _q(), ICP)
    assert q["recommended_next_action"] == "READY_FOR_HUMAN_REVIEW"
    assert q["contradictions"] == []
