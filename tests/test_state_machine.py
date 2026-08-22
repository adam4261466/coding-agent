"""Explicit outreach state machine: legal moves, illegal moves, opt-out,
terminal states, and the audit log."""

import pytest

from linkedin_intelligence.store import Store
from linkedin_intelligence.outreach import state_machine as sm


def _cp(store, pid="p1", cid="c1"):
    cp = {"campaign_id": cid, "prospect_id": pid,
          "status": "CAMPAIGN_ASSIGNED", "priority": 50}
    store.assign_campaign_prospect(cp)
    return cp


def test_legal_transition_moves_state_and_logs(tmp_path):
    store = Store(str(tmp_path / "sm.db"))
    try:
        cp = _cp(store)
        out = sm.transition(store, cp, "MESSAGE_PENDING", event="batch_start")
        assert out["status"] == "MESSAGE_PENDING"
        events = store.outreach_events("p1", "c1")
        assert len(events) == 1
        assert events[0]["from_state"] == "CAMPAIGN_ASSIGNED"
        assert events[0]["to_state"] == "MESSAGE_PENDING"
    finally:
        store.close()


def test_illegal_transition_raises(tmp_path):
    store = Store(str(tmp_path / "sm2.db"))
    try:
        cp = _cp(store)
        with pytest.raises(ValueError):
            sm.transition(store, cp, "SENT", event="skip")  # skips a stage
    finally:
        store.close()


def test_opt_out_allowed_from_active_state(tmp_path):
    store = Store(str(tmp_path / "sm3.db"))
    try:
        cp = _cp(store)
        store.assign_campaign_prospect({**cp, "status": "AWAITING_RESPONSE"})
        cp2 = store.get_campaign_prospect("c1", "p1")
        assert sm.can_transition(cp2["status"], "DO_NOT_CONTACT")
        sm.transition(store, cp2, "DO_NOT_CONTACT", event="opt_out")
        assert store.get_campaign_prospect("c1", "p1")["status"] == "DO_NOT_CONTACT"
    finally:
        store.close()


def test_terminal_states_are_frozen(tmp_path):
    store = Store(str(tmp_path / "sm4.db"))
    try:
        cp = _cp(store)
        store.set_campaign_prospect_status("c1", "p1", "CUSTOMER")
        cp2 = store.get_campaign_prospect("c1", "p1")
        assert not sm.can_transition(cp2["status"], "SENT")
        with pytest.raises(ValueError):
            sm.transition(store, cp2, "SENT", event="bad")
    finally:
        store.close()


def test_full_happy_path_funnel_is_valid():
    path = [
        "DISCOVERED", "QUALIFIED", "HUMAN_APPROVED", "CAMPAIGN_ASSIGNED",
        "MESSAGE_PENDING", "MESSAGE_GENERATED", "MESSAGE_REVIEW",
        "APPROVED_TO_SEND", "SENT", "AWAITING_RESPONSE", "RESPONDED",
        "CONVERSATION", "INTERESTED", "LINK_SHARED", "VISITED",
        "SIGNUP_STARTED", "SIGNED_UP", "ACTIVATED", "CUSTOMER",
    ]
    for a, b in zip(path, path[1:]):
        assert sm.can_transition(a, b), f"{a} -> {b} should be legal"


def test_unknown_state_rejected():
    with pytest.raises(ValueError):
        sm.validate_state("NOT_A_STATE")
