"""Reply classification and conversation state transitions.

The classifier turns an inbound reply into a structured label set. The state
transition that follows is DETERMINISTIC - the LLM classifies, the code
decides. An objection always routes to human-approved response drafting,
never to an automatic rebuttal.
"""


from ..timeutil import iso as _now_iso
import json
import re
import requests
from datetime import datetime, timezone

from ..utils import load_icp
from .message_generator import build_context
from .objections import objection_category, OBJECTION_CATEGORIES
from .state_machine import transition, validate_state

DEFAULT_MODEL = "gemma4:31b-cloud"
DEFAULT_BASE_URL = "http://localhost:11434"

INTENTS = ("question", "interest", "objection", "opt_out", "not_interested",
           "follow_up", "off_topic", "unknown")


def classify_prompt(context: dict, reply: str) -> str:
    return f"""Classify this inbound reply to our outreach message.

CONTEXT:
{json.dumps(context, ensure_ascii=False, indent=2)}

INBOUND REPLY:
{reply}

Return ONLY a JSON object with exactly these keys:
{{
  "intent": one of {", ".join(INTENTS)},
  "sentiment": "positive" or "neutral" or "negative",
  "pain_signal": "present" or "absent" or "unknown",
  "commercial_intent": "high" or "medium" or "low" or "none",
  "objection": one of {", ".join(OBJECTION_CATEGORIES)} or null if no objection,
  "confidence": 0.0-1.0,
  "summary": "one-sentence plain-language summary"
}}
Base everything on the reply text and the evidence in context. If the reply
is ambiguous, use intent "unknown" and confidence below 0.5. Never invent
intent the reply does not express."""


def _parse(text: str) -> dict:
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _sanitize(parsed: dict) -> dict:
    intent = str(parsed.get("intent", "unknown")).strip().lower()
    if intent not in INTENTS:
        intent = "unknown"
    return {
        "intent": intent,
        "sentiment": str(parsed.get("sentiment", "unknown")).lower(),
        "pain_signal": str(parsed.get("pain_signal", "unknown")).lower(),
        "commercial_intent": str(parsed.get("commercial_intent", "none")).lower(),
        "objection": objection_category(parsed.get("objection") or "unknown"),
        "confidence": round(float(parsed.get("confidence") or 0.0), 2),
        "summary": str(parsed.get("summary", ""))[:300],
    }


def classify_reply(store, prospect: dict, campaign: dict, reply: str,
                   model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                   timeout: int = 120) -> dict:
    """Classify one inbound reply (LLM) and return the structured labels."""
    context = build_context(store, prospect, campaign)
    context["_reply"] = reply
    prompt = classify_prompt(context, reply)
    payload = {
        "model": model,
        "think": False,
        "messages": [
            {"role": "system",
             "content": "You classify LinkedIn reply messages into structured "
                        "labels. Output only valid JSON. Unknown is valid."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.0},
    }
    try:
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
        if resp.status_code != 200:
            return _sanitize({"intent": "unknown", "confidence": 0.0,
                              "summary": f"classifier: ollama http {resp.status_code}"})
        text = resp.json().get("message", {}).get("content", "")
        return _sanitize(_parse(text))
    except requests.exceptions.RequestException:
        return _sanitize({"intent": "unknown", "confidence": 0.0,
                          "summary": "classifier: ollama unreachable"})


def next_state(classification: dict) -> str:
    """Deterministic mapping from classification to the next state-machine
    state. This is the ONLY place reply intent is translated to a state."""
    intent = classification.get("intent", "unknown")
    if intent == "opt_out":
        return "DO_NOT_CONTACT"
    if intent == "not_interested":
        return "NOT_INTERESTED"
    if intent == "objection":
        return "CONVERSATION"
    if intent == "interest":
        return "INTERESTED"
    if intent in ("question", "follow_up", "off_topic"):
        return "CONVERSATION"
    # Unknown / ambiguous -> conservative: keep the thread open, human reviews.
    return "CONVERSATION"


def record_reply(store, prospect: dict, campaign: dict, reply: str,
                 classification: dict = None, model: str = DEFAULT_MODEL) -> dict:
    """Persist an inbound reply + classification and advance the state machine.
    Returns the classification dict."""
    if classification is None:
        classification = classify_reply(store, prospect, campaign, reply,
                                        model=model)
    classification["raw"] = reply
    store.save_conversation({
        "prospect_id": prospect["prospect_id"],
        "campaign_id": campaign["campaign_id"],
        "intent": classification["intent"],
        "sentiment": classification.get("sentiment"),
        "pain_signal": classification.get("pain_signal"),
        "commercial_intent": classification.get("commercial_intent"),
        "objection": classification.get("objection"),
        "confidence": classification.get("confidence"),
        "raw": json.dumps({"reply": reply, "summary": classification.get("summary", "")},
                          ensure_ascii=False),
    })
    cp = store.get_campaign_prospect(campaign["campaign_id"],
                                     prospect["prospect_id"])
    if cp:
        # Route every inbound reply through RESPONDED -> CONVERSATION, then to
        # the classified outcome. The state machine is the single source of
        # truth; classification only picks the destination.
        if cp.get("status") in ("SENT", "AWAITING_RESPONSE"):
            cp = transition(store, cp, "RESPONDED", event="reply_received")
        if cp.get("status") == "RESPONDED":
            cp = transition(store, cp, "CONVERSATION",
                            event="classify:" + classification.get("intent", "unknown"))
        target = next_state(classification)
        if target and cp.get("status") != target:
            try:
                transition(store, cp, target,
                           event="classify:" + classification.get("intent", "unknown"),
                           note=classification.get("summary"))
            except ValueError:
                pass
    return classification


def prepare_objection_draft(store, prospect: dict, campaign: dict,
                            classification: dict, model: str = DEFAULT_MODEL,
                            base_url: str = DEFAULT_BASE_URL,
                            timeout: int = 300) -> dict:
    """Draft a reply to an objection. The draft enters the normal approval
    queue (a human must approve it before it can be sent). NEVER auto-sends."""
    category = classification.get("objection", "unknown")
    from .message_generator import generate_message, build_context, build_prompt
    from .message_strategy import get_strategy
    icp = load_icp()
    product = icp.get("product", {})
    strategy = get_strategy("conversation_first")
    context = build_context(store, prospect, campaign)
    context["_task"] = (
        f"Respond to this objection in a way that is helpful, not argumentative. "
        f"Objection category: {category}. Handling guidance: "
        f"{ob_guidance(category)} "
        f"The reply must never repeat unsupported claims. Keep it under 60 words "
        f"and end with one genuine question."
    )
    from .message_generator import parse_message, _sanitize
    prompt = build_prompt(context, strategy)
    payload = {
        "model": model,
        "think": False,
        "messages": [
            {"role": "system",
             "content": "You draft cautious, evidence-grounded replies to "
                        "objections. Output only valid JSON. Never argue, never "
                        "invent facts."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.1},
    }
    msg = {
        "message_id": "obj_" + __import__("uuid").uuid4().hex[:10],
        "prospect_id": prospect["prospect_id"],
        "campaign_id": campaign["campaign_id"],
        "strategy": "objection_response:" + category,
        "version": "draft",
        "text": "",
        "claims": [],
        "evidence_used": [],
        "confidence": 0.0,
        "approved": False,
        "validation": [],
        "status": "pending_review",
        "created_at": _now_iso(),
    }
    try:
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
        if resp.status_code == 200:
            text = resp.json().get("message", {}).get("content", "")
            parsed = _sanitize(parse_message(text), "objection_response")
            msg.update(parsed)
            msg["validation"] = ["objection draft"]
    except requests.exceptions.RequestException:
        msg["validation"] = ["objection draft: ollama unreachable"]
    store.save_message(msg)
    return msg


def ob_guidance(category: str) -> str:
    from .objections import guidance
    return guidance(category)
