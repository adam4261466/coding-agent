"""LLM message generation with strict provenance.

The message generator is the ONLY place an LLM writes outreach copy. It sees a
structured context (never the raw DB or the CSV) and must return a structured
object that carries every claim it made plus the exact evidence each claim
rests on. Messages are immutable and versioned - a regeneration creates v2,
it never overwrites v1.

The validator is applied to every generated message before it enters the
approval queue.
"""


from ..timeutil import iso as _now_iso
import json
import re
import uuid
import requests
from datetime import datetime, timezone

from ..utils import load_icp
from ..research.evidence import evidence_block, offline_evidence
from .message_strategy import get_strategy

DEFAULT_MODEL = "gemma4:31b-cloud"
DEFAULT_BASE_URL = "http://localhost:11434"


def build_context(store, prospect: dict, campaign: dict) -> dict:
    """The ONLY data the generator may see. Everything is labelled so the
    model cannot mistake inference for fact."""
    icp = load_icp()
    product = icp.get("product", {})
    evidence = evidence_block(store, prospect) or offline_evidence(prospect)
    convs = prospect.get("conversation_history") or []

    first_name = prospect.get("first_name") or ""
    if not first_name:
        full = (prospect.get("full_name") or "").split()
        if full:
            first_name = full[0]

    return {
        "prospect": {
            "first_name": first_name,
            "full_name": prospect.get("full_name", ""),
            "position": prospect.get("raw_position") or prospect.get("normalized_position") or "unknown role",
            "company": prospect.get("current_company") or "unknown company",
            "segments": prospect.get("segments") or [],
            "pain_state": prospect.get("pain_state") or "not_researched",
            "segmentation_status": prospect.get("segmentation_status") or "unsegmented",
            "missing_signals": prospect.get("missing_signals") or [],
        },
        "relationship": {
            "relationship_score": prospect.get("relationship_score"),
            "connection_status": prospect.get("connection_status"),
            "prior_conversations": [
                {"topic": c[:200] if isinstance(c, str) else c}
                for c in convs[:3]
            ],
        },
        "evidence": [
            {
                "claim": e.get("claim"),
                "source": f"{e.get('source')}/{e.get('collector')}",
                "confidence": e.get("confidence"),
                "source_type": e.get("source_type"),
            }
            for e in evidence
            if e.get("claim")
        ],
        "product": {
            "name": product.get("name", "our product"),
            "summary": product.get("summary", ""),
            "problems": product.get("problems", []),
            "outcome": product.get("outcome", ""),
        },
        "campaign": {
            "name": campaign.get("name"),
            "objective": campaign.get("objective"),
        },
        "rules": [
            "Never claim something not backed by the evidence list.",
            "Never reference a prior conversation, connection, or interaction "
            "that does not appear in relationship.prior_conversations.",
            "Never invent the prospect's pain, tools, or projects.",
            "Address the prospect by their real first name; if it is empty "
            "use no name at all.",
            "Do not pretend the product solved a problem the prospect never "
            "said they have.",
            "claims[] must contain exactly the factual statements you wrote; "
            "evidence_used[] must contain the claim strings that back them.",
        ],
    }


def build_prompt(context: dict, strategy: dict) -> str:
    s = strategy
    return f"""Write ONE LinkedIn outreach message using the strategy below.

STRATEGY: {s['label']}
{s['description']}
Instructions:
{chr(10).join('- ' + i for i in s['instructions'])}

CONTEXT (labelled; inference is not fact):
{json.dumps(context, ensure_ascii=False, indent=2)}

RULES (non-negotiable):
{chr(10).join('- ' + r for r in context['rules'])}

Return ONLY a JSON object with exactly these keys:
{{
  "strategy": "{context['campaign'].get('name','') or s['label']}",
  "message": "the message text",
  "evidence_used": ["exact claim strings from the context you relied on"],
  "claims": ["every factual claim you made in the message"],
  "confidence": 0.0-1.0
}}
No prose outside the JSON."""


def parse_message(text: str) -> dict:
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


def _sanitize(parsed: dict, strategy_name: str) -> dict:
    return {
        "strategy": strategy_name,
        "message": str(parsed.get("message", "")).strip(),
        "evidence_used": [str(x)[:300] for x in parsed.get("evidence_used", [])],
        "claims": [str(x)[:300] for x in parsed.get("claims", [])],
        "confidence": round(float(parsed.get("confidence") or 0.0), 2),
    }


def _next_version(store, prospect_id: str, campaign_id: str) -> str:
    versions = [m.get("version") for m in store.messages_for(prospect_id, campaign_id)
                if m.get("version")]
    nums = []
    for v in versions:
        m = re.search(r"(\d+)$", str(v))
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"v{n}"


def generate_message(store, prospect: dict, campaign: dict,
                     strategy: str = None, model: str = DEFAULT_MODEL,
                     base_url: str = DEFAULT_BASE_URL,
                     timeout: int = 300) -> dict:
    """Generate one message, persist it as MESSAGE_REVIEW, and return it.
    Creates a new immutable version on every call."""
    strategy_name = get_strategy(strategy).get("label")
    context = build_context(store, prospect, campaign)
    prompt = build_prompt(context, get_strategy(strategy))

    payload = {
        "model": model,
        "think": False,
        "messages": [
            {"role": "system",
             "content": "You write cautious, evidence-grounded LinkedIn "
                        "outreach copy. Output only valid JSON. Never invent "
                        "facts, relationships, or pain."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.1},
    }

    msg = {
        "message_id": "msg_" + uuid.uuid4().hex[:10],
        "prospect_id": prospect["prospect_id"],
        "campaign_id": campaign["campaign_id"],
        "strategy": strategy_name,
        "version": _next_version(store, prospect["prospect_id"],
                                 campaign["campaign_id"]),
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
        if resp.status_code != 200:
            msg["validation"] = [f"generator: ollama http {resp.status_code}"]
            store.save_message(msg)
            return msg
        data = resp.json()
        text = data.get("message", {}).get("content", "")
        parsed = _sanitize(parse_message(text), strategy_name)
        if "message" in parsed:
            parsed["text"] = parsed.pop("message")
        msg.update(parsed)
        msg["validation"] = ["generated"]
        msg["status"] = "generated"
    except requests.exceptions.RequestException:
        msg["validation"] = ["generator: ollama unreachable"]
        msg["status"] = "generator_error"

    store.save_message(msg)
    return msg
