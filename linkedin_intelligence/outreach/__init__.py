"""Phase 3: outreach & conversion engine.

Deterministic campaign assignment, versioned LLM message generation with
strict evidence provenance, a validation gate before any approval, explicit
state machine, human approval, reply classification, and attribution.

Order of operations per prospect:
  eligible -> assigned -> message generated -> validated -> human approved
  -> sent -> awaiting response -> (reply classification -> state change)
  -> website events -> activation/customer
"""

from . import state_machine, eligibility, campaign, message_strategy, \
    message_generator, validator, approval, sequence, objections, \
    conversation, attribution, analytics  # noqa: F401

__all__ = [
    "state_machine", "eligibility", "campaign", "message_strategy",
    "message_generator", "validator", "approval", "sequence", "objections",
    "conversation", "attribution", "analytics",
]
