"""Relationship + conversation intelligence, computed in code (no LLM).

Conversation classification is structured:
  relationship_state, sentiment, product_interest, pain_signal,
  commercial_intent, confidence
Keyword detection is ONLY a deterministic fallback. When nothing matches, a
field stays "unknown" - the system never manufactures intent.
"""

import os
import re
from datetime import datetime

from ..utils import load_icp, load_privacy, strip_html, collapse_ws
from .normalizer import normalize_name, normalize_url


def _parse_dt(value: str):
    if not value:
        return None
    v = collapse_ws(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%d %b %Y", "%b %d %Y", "%m/%d/%y, %I:%M %p",
                "%m/%d/%Y, %I:%M %p", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _days_ago(dt: datetime) -> float:
    return (datetime.now() - dt).total_seconds() / 86400.0


def _hit(text: str, keywords: list) -> list:
    t = text.lower()
    return [kw for kw in keywords if kw.lower() in t]


def classify_conversation(messages, own_full_name: str) -> dict:
    """Structured classification of one conversation thread."""
    icp = load_icp()
    kw = icp.get("conversation_keywords", {})
    legacy = icp.get("commercial_signal", {})

    own = own_full_name.lower()
    texts = [f"{m.subject} {m.content}" for m in messages]
    all_text = " ".join(texts)
    declined = _hit(all_text, legacy.get("declined", []))

    state_hits = {
        "sentiment_positive": _hit(all_text, kw.get("sentiment_positive", [])),
        "sentiment_negative": _hit(all_text, kw.get("sentiment_negative", [])),
        "product_interest": _hit(all_text, kw.get("product_interest", [])),
        "pain_signal": _hit(all_text, kw.get("pain_signal", [])),
        "declined": declined,
    }

    if declined:
        sentiment = "negative"
        product_interest = "none"
        pain_signal = "none"
        commercial_intent = "none"
        confidence = 0.8
    else:
        sentiment = ("negative" if state_hits["sentiment_negative"]
                     else "positive" if state_hits["sentiment_positive"]
                     else "unknown")
        # product interest: explicit vs possible via legacy keyword buckets
        explicit = _hit(all_text, legacy.get("explicit_interest", []))
        possible = _hit(all_text, legacy.get("possible_interest", []))
        if explicit:
            product_interest = "explicit"
        elif possible or state_hits["product_interest"]:
            product_interest = "possible"
        else:
            product_interest = "unknown"
        pain_signal = ("explicit" if state_hits["pain_signal"] else "unknown")

        if explicit and state_hits["pain_signal"]:
            commercial_intent = "explicit"
        elif explicit or possible or state_hits["pain_signal"]:
            commercial_intent = "possible"
        else:
            commercial_intent = "unknown"

        matched = len(state_hits["product_interest"]) + len(state_hits["pain_signal"]) \
            + len(explicit) + len(possible)
        confidence = 0.7 if explicit else 0.5 if matched else 0.25

    has_reply = any(m.from_name and m.from_name.lower() != own for m in messages)
    last = messages[-1]
    last_from_self = last.from_name and last.from_name.lower() == own
    if declined:
        relationship_state = "declined"
    elif has_reply:
        relationship_state = "conversation"
    elif not last_from_self:
        relationship_state = "inbound"
    else:
        relationship_state = "no_response"

    return {
        "relationship_state": relationship_state,
        "sentiment": sentiment,
        "product_interest": product_interest,
        "pain_signal": pain_signal,
        "commercial_intent": commercial_intent,
        "confidence": round(confidence, 2),
    }


def build_conversations(messages, own_full_name: str) -> list:
    """Group messages into conversations and attach intelligence fields."""
    privacy = load_privacy()
    summary_chars = int(privacy.get("conversation_summary_chars", 300))

    groups = {}
    for m in messages:
        cid = (m.conversation_id or "").strip() or f"anon:{m.from_name}|{m.to_name}"
        groups.setdefault(cid, []).append(m)

    out = []
    for cid, msgs in groups.items():
        msgs.sort(key=lambda m: (m.date or ""))
        own = own_full_name.lower()
        others = set()
        for m in msgs:
            if m.from_name and m.from_name.lower() != own:
                others.add(collapse_ws(m.from_name))
            if m.to_name and m.to_name.lower() != own:
                others.add(collapse_ws(m.to_name))

        intelligence = classify_conversation(msgs, own_full_name)
        last = msgs[-1]
        last_from_self = last.from_name and last.from_name.lower() == own

        topic = strip_html(msgs[0].subject or msgs[0].content)[:120]
        last_msg = strip_html(last.content)[:summary_chars]
        dates = [m.date for m in msgs if m.date]
        last_dt = _parse_dt(last.date)
        follow_up_needed = (
            intelligence["relationship_state"] in ("conversation", "no_response")
            and intelligence["commercial_intent"] not in ("none",)
            and last_from_self
        )
        out.append({
            "conversation_id": cid,
            "participants": sorted(others),
            "first_date": dates[0] if dates else None,
            "last_date": last.date or None,
            "last_date_dt": last_dt,
            "direction": "outbound" if last_from_self else "inbound",
            "topic": topic,
            "message_count": len(msgs),
            "last_message": last_msg,
            "response_received": any(
                m.from_name and m.from_name.lower() != own for m in msgs),
            "intelligence": intelligence,
            # legacy flat signal kept for backward compatibility
            "commercial_signal": {
                "explicit_interest": "explicit_interest",
                "possible_interest": "possible_interest",
                "declined": "declined",
            }.get(intelligence["commercial_intent"], "none" if intelligence["commercial_intent"] in ("none",) else "unknown"),
            "relationship_state": intelligence["relationship_state"],
            "follow_up_needed": follow_up_needed,
            "people": {"from": msgs[-1].from_name, "to": msgs[-1].to_name},
        })
    return out


def match_conversations(convos, url: str, full_name: str) -> list:
    """Attach conversations to a prospect by URL or normalized name."""
    nurl = normalize_url(url)
    nname = normalize_name(full_name)
    matched = []
    for c in convos:
        url_hit = any(normalize_url(u) == nurl for u in
                      (c["people"]["from"], c["people"]["to"])) if nurl else False
        name_hit = nname and (
            nname in normalize_name(c["people"]["from"])
            or nname in normalize_name(c["people"]["to"]))
        if url_hit or name_hit:
            matched.append(c)
    return matched


def relationship_components(prospect, convos: list, invitations: list,
                            shared_school: bool = False) -> dict:
    """Deterministic relationship score components for one prospect."""
    comp = {
        "existing_connection": 30,
        "previous_conversation": 0,
        "recent_interaction": 0,
        "shared_school": 15 if shared_school else 0,
        "commercial_signal": 0,
        "recent_outbound_penalty": 0,
    }

    if convos:
        comp["previous_conversation"] = 20
        last_dt = max((c["last_date_dt"] for c in convos if c["last_date_dt"]), default=None)
        if last_dt:
            days = _days_ago(last_dt)
            if days <= 90:
                comp["recent_interaction"] = 15
            elif days <= 180:
                comp["recent_interaction"] = 10
            elif days <= 365:
                comp["recent_interaction"] = 5
        intents = [c["intelligence"]["commercial_intent"] for c in convos]
        if any(i == "explicit" for i in intents):
            comp["commercial_signal"] = 15
        elif any(i == "possible" for i in intents):
            comp["commercial_signal"] = 5
        if any(c["intelligence"]["relationship_state"] == "declined" for c in convos):
            comp["commercial_signal"] = -25
        for c in convos:
            if c["direction"] == "outbound" and c["last_date_dt"]:
                if _days_ago(c["last_date_dt"]) <= 30:
                    comp["recent_outbound_penalty"] = -20
                    break

    score = sum(comp.values())
    return {"score": max(0, min(100, score)), "components": comp}


def engagement_score(convos: list, config: dict) -> tuple:
    """Interaction-based engagement score (0-100) + its components."""
    e = config["icp"].get("engagement", {})
    if not convos:
        return 0, {"base": 0, "reason": "no conversation"}
    score = int(e.get("has_conversation_base", 40))
    parts = {"has_conversation": score}
    if any(c["response_received"] for c in convos):
        score += int(e.get("response_received", 20))
        parts["response_received"] = int(e.get("response_received", 20))
    if any(c["direction"] == "inbound" for c in convos):
        score += int(e.get("inbound_last", 10))
        parts["inbound_last"] = int(e.get("inbound_last", 10))
    if max((c["message_count"] for c in convos), default=0) >= 3:
        score += int(e.get("message_count_3plus", 10))
        parts["message_count_3plus"] = int(e.get("message_count_3plus", 10))
    last_dt = max((c["last_date_dt"] for c in convos if c["last_date_dt"]), default=None)
    if last_dt:
        days = _days_ago(last_dt)
        if days <= 90:
            score += int(e.get("recency_90d", 20))
            parts["recency"] = "90d"
        elif days <= 180:
            score += int(e.get("recency_180d", 10))
            parts["recency"] = "180d"
        elif days <= 365:
            score += int(e.get("recency_365d", 5))
            parts["recency"] = "365d"
    return max(0, min(100, score)), parts


def buying_signal(convos: list, config: dict) -> tuple:
    """Structured buying signal from conversation intelligence.

    Returns (score 0-100, intent, confidence). "unknown" intent -> 0 score.
    """
    bs = config["icp"].get("buying_signal", {})
    if not convos:
        return 0, "unknown", 0.0
    intents = [c["intelligence"]["commercial_intent"] for c in convos]
    if any(i == "explicit" for i in intents):
        intent = "explicit"
    elif any(i == "possible" for i in intents):
        intent = "possible"
    elif any(i == "none" for i in intents):
        intent = "none"
    else:
        intent = "unknown"
    conf = max((c["intelligence"]["confidence"] for c in convos), default=0.0)
    return int(bs.get(intent, bs.get("unknown", 0))), intent, round(conf, 2)


def match_invitations(prospect, invitations: list) -> list:
    nname = normalize_name(prospect.full_name)
    nurl = normalize_url(prospect.url)
    out = []
    for inv in invitations:
        hit = False
        reason = ""
        if nurl and (nurl == normalize_url(inv.inviter_url)
                     or nurl == normalize_url(inv.invitee_url)):
            hit, reason = True, "url"
        elif nname and (nname in normalize_name(inv.from_name)
                        or nname in normalize_name(inv.to_name)):
            hit, reason = True, "name"
        if hit:
            out.append({"direction": inv.direction, "sent_at": inv.sent_at,
                        "matched_by": reason})
    return out
