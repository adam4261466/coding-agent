"""Deterministic message validator - the quality gate before any approval.

Runs BEFORE the approval queue. Rejects or flags a message for: identity
issues, unsupported claims (claim not backed by any evidence string), fake
personalization (references a conversation/relationship the prospect has no
record of), duplicates (same text already sent/approved to the same
prospect), length/style problems, and eligibility. The LLM never self-reviews
its own copy.
"""

import re

from .eligibility import is_blocked
from .message_strategy import STRATEGIES

MAX_WORDS = 120
_STRATEGY_KEYS = {s.lower() for s in STRATEGIES}
_STRATEGY_LABELS = {v["label"].lower() for v in STRATEGIES.values()}


def _freq(text: str) -> list:
    return [w.lower().strip(".,!?;:()[]") for w in re.findall(r"\w+", text or "")
            if w.strip(".,!?;:()[]")]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _evidence_strings(prospect: dict, store) -> list:
    from ..research.evidence import evidence_block, offline_evidence
    items = evidence_block(store, prospect) or offline_evidence(prospect)
    return [str(e.get("claim") or "").strip().lower() for e in items if e.get("claim")]


def _prior_conversations(prospect: dict) -> list:
    convs = prospect.get("conversation_history") or []
    return [str(c).lower() if isinstance(c, str) else str(c.get("topic") or "").lower()
            for c in convs]


def validate_message(store, prospect: dict, campaign: dict, msg: dict) -> dict:
    """Return `msg` with `validation` = [ok, fail1, fail2, ...] and
    `status` = "approved" | "needs_rework" | "rejected". Idempotent."""
    checks = []
    text = msg.get("text") or ""

    # 1. Identity: non-empty, addressed to the right person.
    if not text.strip():
        checks.append("fail: message is empty")
    if len(text.strip()) > 0 and len(text.strip()) < 10:
        checks.append("fail: message too short to be meaningful")
    first = prospect.get("first_name") or ""
    if not first:
        full = (prospect.get("full_name") or "").split()
        if full:
            first = full[0]
    if first and first.lower() not in text.lower() and "{{" not in text:
        # A missing name alone is not a hard fail (template-style messages),
        # but a wrong-name is.
        checks.append("flag: no first name used")
    others = {p.get("first_name") for p in (prospect.get("_other_names") or [])}
    for other in others:
        if other and other != first and other.lower() in text.lower():
            checks.append(f"fail: mentions a different name ({other})")

    # 2. Evidence: every claim must be traceable to an evidence string.
    evidence_src = _evidence_strings(prospect, store)
    claims = [str(c).strip().lower() for c in msg.get("claims", [])]
    unsupported = []
    for claim in claims:
        # Exact or strong substring match against an evidence claim.
        if not any(claim and (claim in ev or ev in claim or
                              _overlap(claim, ev)) for ev in evidence_src):
            unsupported.append(claim)
    if unsupported:
        checks.append(f"fail: unsupported claim(s): {unsupported[:3]}")

    # 3. Fake personalization: prior-conversation references must exist.
    convs = _prior_conversations(prospect)
    low = text.lower()
    for ref in ("we talked", "our conversation", "we spoke", "we chatted",
                "we discussed", "we connected", "we met"):
        if ref in low:
            if not convs:
                checks.append(f"fail: references '{ref}' but no prior conversation exists")
            else:
                checks.append(f"ok: '{ref}' backed by prior conversation")

    # 4. Duplicates against the prospect's own message history.
    for other in store.messages_for(prospect_id=prospect["prospect_id"]):
        if other.get("message_id") == msg.get("message_id"):
            continue
        if _normalize(other.get("text")) == _normalize(text) and text.strip():
            checks.append("fail: duplicate of an existing message")

    # 5. Length / style.
    words = len(_freq(text))
    if words > MAX_WORDS:
        checks.append(f"fail: too long ({words} words > {MAX_WORDS})")
    if text.count("?") > 3:
        checks.append("flag: excessive question marks")
    if "!!" in text or text.endswith("!!"):
        checks.append("flag: excessive punctuation")
    strategy = str(msg.get("strategy") or "").lower()
    if strategy and strategy not in _STRATEGY_KEYS and strategy not in _STRATEGY_LABELS:
        checks.append(f"flag: unknown strategy '{msg.get('strategy')}'")

    # 6. Hard block check (not full eligibility: an assigned prospect is
    #    in an active conversation by design). A message for a blocked
    #    prospect is a fail.
    if is_blocked(store, prospect):
        checks.append("fail: prospect is blocked (do_not_contact/exclusion)")

    # 7. Evidence_used must be a subset-ish of the message's claims context.
    if not msg.get("evidence_used") and claims:
        checks.append("flag: no evidence_used recorded")

    failed = [c for c in checks if c.startswith("fail:")]
    if failed:
        status = "rejected" if len(failed) >= 2 else "needs_rework"
    else:
        status = "approved"

    msg["validation"] = checks or ["ok"]
    msg["status"] = status
    return msg


def _overlap(a: str, b: str) -> bool:
    """Token overlap heuristic for claim-vs-evidence matching: a claim is
    traceable to an evidence string when it covers most of the evidence's
    tokens. Filler words in the claim don't hurt the score."""
    sa, sb = set(_freq(a)), set(_freq(b))
    if not sb:
        return False
    return len(sa & sb) / len(sb) >= 0.6


def validate_generated(store, prospect: dict, campaign: dict, msg: dict) -> dict:
    """Validates a just-generated message and persists the verdict. Used by
    the generator pipeline so every message carries its gate result."""
    msg = validate_message(store, prospect, campaign, msg)
    store.save_message(msg)
    return msg
