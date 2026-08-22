"""End-to-end outreach pipeline: the message production line.

assigned prospect (MESSAGE_PENDING)
  -> generate_message()          (LLM, versioned, immutable)
  -> validator.validate_generated (deterministic quality gate)
  -> approval queue               (human decides)
  -> approve()                    -> APPROVED_TO_SEND
  -> sequence.mark_sent()         -> AWAITING_RESPONSE

Nothing is ever sent to a person automatically; the human clicks through the
approval queue and the "mark sent" action.
"""

import uuid
from datetime import datetime, timezone

from . import validator
from .state_machine import transition
from .message_generator import generate_message
from .approval import approval_queue, approve, reject
from .sequence import mark_sent, make_follow_up_eligible, due_follow_ups
from .conversation import record_reply, prepare_objection_draft


def produce_message(store, campaign: dict, campaign_prospect: dict,
                    model: str = None, base_url: str = None) -> dict:
    """Generate + validate + persist one message for one assigned prospect.
    Returns the message dict (status approved/needs_rework/rejected)."""
    prospect = store.get_prospect(campaign_prospect["prospect_id"])
    if not prospect:
        raise ValueError(f"unknown prospect {campaign_prospect['prospect_id']}")
    msg = generate_message(store, prospect, campaign,
                           strategy=campaign_prospect.get("assigned_strategy"),
                           model=model, base_url=base_url)
    validator.validate_generated(store, prospect, campaign, msg)
    try:
        cp = transition(store, campaign_prospect, "MESSAGE_GENERATED",
                        event="generated", note=msg["message_id"])
        transition(store, cp, "MESSAGE_REVIEW",
                   event="validated", note=msg["message_id"])
    except ValueError:
        pass
    return msg


def produce_batch(store, campaign: dict, limit: int = 10,
                  model: str = None, base_url: str = None) -> dict:
    """Produce messages for the next MESSAGE_PENDING prospects in a campaign."""
    from .campaign import next_batch
    batch = next_batch(store, campaign, limit)
    produced = []
    for cp in batch:
        msg = produce_message(store, campaign, cp, model=model, base_url=base_url)
        produced.append(msg)
    return {
        "campaign_id": campaign["campaign_id"],
        "started": len(batch),
        "generated": len(produced),
        "messages": produced,
    }


def register_reply(store, campaign: dict, prospect_id: str, reply: str,
                   model: str = None) -> dict:
    """Human pastes an inbound reply; it is classified, state advances, and
    if it is an objection a draft is prepared for the approval queue."""
    prospect = store.get_prospect(prospect_id)
    if not prospect:
        raise ValueError(f"unknown prospect {prospect_id}")
    from .conversation import classify_reply
    classification = classify_reply(store, prospect, campaign, reply, model=model)
    record_reply(store, prospect, campaign, reply, classification, model=model)
    result = {"classification": classification}
    if classification.get("objection") != "unknown" and \
            classification.get("objection") is not None:
        draft = prepare_objection_draft(store, prospect, campaign, classification,
                                        model=model)
        result["objection_draft"] = draft
    return result
