"""Every prospect ends research in exactly one next-action state."""

from linkedin_intelligence.research.next_action import assign_next_action


def _p(signal="unknown", status="new", convos=0, last_contacted=None):
    return {"commercial_intent": signal, "status": status,
            "conversation_count": convos, "last_contacted": last_contacted}


def test_declined_signal_wins_over_model_suggestion():
    p = _p(signal="declined")
    q = {"recommended_next_action": "READY_FOR_HUMAN_REVIEW", "confidence": 0.9}
    assert assign_next_action(p, q) == "DO_NOT_CONTACT"


def test_high_confidence_review_reaches_the_queue():
    p = _p()
    q = {"recommended_next_action": "READY_FOR_HUMAN_REVIEW", "confidence": 0.8}
    assert assign_next_action(p, q) == "READY_FOR_HUMAN_REVIEW"


def test_low_confidence_review_is_demoted_to_research_more():
    p = _p()
    q = {"recommended_next_action": "READY_FOR_HUMAN_REVIEW", "confidence": 0.3}
    assert assign_next_action(p, q) == "RESEARCH_MORE"


def test_min_confidence_threshold_is_configurable():
    p = _p()
    q = {"recommended_next_action": "READY_FOR_HUMAN_REVIEW", "confidence": 0.45}
    assert assign_next_action(p, q, min_confidence_for_ready=0.4) == "READY_FOR_HUMAN_REVIEW"
    assert assign_next_action(p, q, min_confidence_for_ready=0.5) == "RESEARCH_MORE"


def test_recent_conversation_with_signal_becomes_follow_up():
    p = _p(signal="possible", convos=2, last_contacted="2026-07-01")
    q = {"recommended_next_action": "RESEARCH_MORE", "confidence": 0.4}
    assert assign_next_action(p, q) == "FOLLOW_UP"


def test_unknown_signal_and_low_confidence_stay_research_more():
    p = _p()
    q = {"recommended_next_action": "RESEARCH_MORE", "confidence": 0.4}
    assert assign_next_action(p, q) == "RESEARCH_MORE"


def test_outreach_also_requires_confidence():
    p = _p()
    q = {"recommended_next_action": "READY_FOR_OUTREACH", "confidence": 0.2}
    assert assign_next_action(p, q) == "RESEARCH_MORE"
