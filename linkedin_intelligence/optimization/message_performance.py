"""Message performance: which message versions actually earned replies. Ties
each reply back to the exact message version that was live when it arrived.
"""

from .evaluator import wilson_ci


def _version_for_reply(store, prospect_id: str, campaign_id: str,
                       reply_created: str):
    msgs = sorted([m for m in store.messages_for(prospect_id, campaign_id)
                   if m.get("status") == "approved_to_send"],
                  key=lambda m: m.get("created_at") or "")
    if not msgs:
        return None
    # The most recent sent message before the reply is the one it answered.
    for m in reversed(msgs):
        if (m.get("created_at") or "") <= (reply_created or "9999"):
            return m
    return msgs[0]


def message_performance(store) -> list:
    by_version = {}
    for m in store.messages_for():
        key = (m.get("campaign_id"), m.get("prospect_id"), m.get("version"))
        by_version.setdefault(key, {"message_id": m["message_id"],
                                    "campaign_id": m.get("campaign_id"),
                                    "prospect_id": m.get("prospect_id"),
                                    "version": m.get("version"),
                                    "strategy": m.get("strategy"),
                                    "sent": False, "replied": 0,
                                    "text": (m.get("text") or "")[:120]})
    for cp in store.campaign_prospects():
        for key, rec in by_version.items():
            if key[0] == cp["campaign_id"] and key[1] == cp["prospect_id"]:
                # A prospect counts as "reached this version" when a message
                # with that version was approved to send and the prospect
                # advanced past AWAITING_RESPONSE.
                rec["sent"] = True
    for c in store.conversations_outreach():
        v = _version_for_reply(store, c.get("prospect_id"), c.get("campaign_id"),
                               c.get("created_at"))
        if not v:
            continue
        key = (c.get("campaign_id"), c.get("prospect_id"), v.get("version"))
        if key in by_version:
            by_version[key]["replied"] += 1

    out = []
    for rec in by_version.values():
        if not rec["sent"]:
            continue
        ci = wilson_ci(rec["replied"], 1)  # per-message sample is 1 prospect
        rec["reply_ci"] = {**ci, "n": 1}
        out.append(rec)
    return out


def repeated_phrases(store, top_n: int = 5) -> list:
    """Over-used message openers across sent messages (flag for the human, no
    automatic change)."""
    from collections import Counter
    openers = Counter()
    for m in store.messages_for(status="approved_to_send"):
        words = (m.get("text") or "").split()
        if words:
            openers[" ".join(words[:4])] += 1
    return [{"opener": k, "count": v} for k, v in openers.most_common(top_n)]
