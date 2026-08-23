"""Permission model: the single source of truth for what the agent may do.

The human decides, once, per person:
  permission : allowed | manual | blocked
  flags      : view_profile, send_connection, send_message, reply, follow_up

The agent never makes eligibility decisions. It only reads this record and
obeys it. A missing record means "manual" with every flag off - the safe
default. Nothing here talks to an LLM.
"""

PERMISSION_LEVELS = ("allowed", "manual", "blocked")

PERMISSION_FLAGS = (
    "view_profile",
    "send_connection",
    "send_message",
    "reply",
    "follow_up",
)

DEFAULT_PERMISSIONS = {
    "permission": "manual",
    "view_profile": False,
    "send_connection": False,
    "send_message": False,
    "reply": False,
    "follow_up": False,
    "blocked": False,
}

# Agent action name -> permission flag that authorizes it.
ACTION_FLAG = {
    "observe_profile": "view_profile",
    "view_profile": "view_profile",
    "observe_conversation": "view_profile",
    "send_connection_request": "send_connection",
    "send_message": "send_message",
    "reply": "reply",
    "follow_up": "follow_up",
}


def normalize(record: dict) -> dict:
    """Coerce a raw store row (or partial dict) into a clean permission
    record with every key present as a bool (except notes)."""
    out = dict(DEFAULT_PERMISSIONS)
    if not record:
        return out
    level = str(record.get("permission") or "manual").lower()
    out["permission"] = level if level in PERMISSION_LEVELS else "manual"
    for flag in PERMISSION_FLAGS:
        out[flag] = bool(record.get(flag))
    out["blocked"] = bool(record.get("blocked")) or \
        out["permission"] == "blocked"
    out["notes"] = record.get("notes")
    out["prospect_id"] = record.get("prospect_id")
    return out


def is_blocked(record) -> bool:
    r = normalize(record)
    return r["blocked"] or r["permission"] == "blocked"


def needs_manual_review(record) -> bool:
    r = normalize(record)
    return (not r["blocked"]) and r["permission"] == "manual"


def allows(record, action: str) -> bool:
    """True only if the record is 'allowed' AND the specific action's flag
    is on AND the person is not blocked."""
    r = normalize(record)
    if r["blocked"] or r["permission"] != "allowed":
        return False
    flag = ACTION_FLAG.get(action)
    if flag is None:
        raise ValueError(f"unknown agent action: {action}")
    return bool(r[flag])


def load_json(path: str) -> dict:
    """Parse a permissions JSON file into {prospect_id: record}.

    Expected shape (keys other than the ones below are rejected):
    {
      "<prospect_id>": {
        "permission": "allowed",
        "permissions": {"view_profile": true, "send_connection": true,
                         "send_message": false, "reply": false,
                         "follow_up": false},
        "notes": "met at conference"
      }
    }
    """
    import json

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("permissions file must be a JSON object keyed by prospect_id")

    ALIASES = {
        "view_profile": "view_profile", "view profile": "view_profile",
        "send_connection": "send_connection",
        "send connection": "send_connection",
        "connect": "send_connection",
        "send_message": "send_message", "send message": "send_message",
        "message": "send_message",
        "reply": "reply",
        "follow_up": "follow_up", "follow up": "follow_up",
        "followup": "follow_up",
    }

    out = {}
    for pid, spec in raw.items():
        if not isinstance(spec, dict):
            raise ValueError(f"{pid}: entry must be an object")
        level = str(spec.get("permission") or "manual").lower()
        if level not in PERMISSION_LEVELS:
            raise ValueError(
                f"{pid}: permission must be one of {PERMISSION_LEVELS}")
        flags = {}
        for key, value in (spec.get("permissions") or {}).items():
            canonical = ALIASES.get(str(key).strip().lower())
            if canonical is None:
                raise ValueError(
                    f"{pid}: unknown permission flag {key!r} "
                    f"(valid: {', '.join(PERMISSION_FLAGS)})")
            flags[canonical] = bool(value)
        notes = spec.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise ValueError(f"{pid}: notes must be a string")
        out[str(pid)] = {"permission": level, **flags,
                         "notes": notes or None}
    return out
