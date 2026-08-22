"""Pain-state semantics: "not researched" is never conflated with "no pain"."""

from linkedin_intelligence.research.pain_state import pain_state


def _p(**kw):
    base = {
        "prospect_id": "p1",
        "commercial_intent": "unknown",
        "status": "new",
        "problem_fit_score": None,
        "research_mode": None,
        "conversation_intelligence": None,
    }
    base.update(kw)
    return base


def test_declined_wins_everything():
    assert pain_state(_p(commercial_intent="declined")) == "declined"
    assert pain_state(_p(status="do_not_contact")) == "declined"


def test_demonstrated_when_problem_fit_is_strong():
    p = _p(problem_fit_score=75)
    assert pain_state(p) == "demonstrated"


def test_plausible_when_problem_fit_is_assessed_but_low():
    p = _p(problem_fit_score=40)
    assert pain_state(p) == "plausible"


def test_not_researched_is_not_no_pain():
    assert pain_state(_p()) == "not_researched"


def test_researched_without_evidence_is_no_pain_evidence():
    p = _p(research_mode="live_browser")
    assert pain_state(p) == "no_pain_evidence"
    assert pain_state(_p(), qualification={"fit_score": 60}) == "no_pain_evidence"


def test_explicit_conversation_pain_signal_is_demonstrated():
    p = _p(conversation_intelligence={"pain_signal": "explicit"})
    assert pain_state(p) == "demonstrated"
