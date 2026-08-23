"""Prospect queue: the deterministic order people are worked.

Splits every prospect in the store into three buckets using the
human-owned permission records:

  ALLOWED CONTACT - permission == 'allowed'
  MANUAL REVIEW   - permission == 'manual' or no record (safe default)
  BLOCKED PEOPLE  - permission == 'blocked' or the blocked flag

Within a bucket, ordering is by outreach priority (total_score DESC),
matching the existing prospects table ordering. No LLM involved.
"""

from .permissions import normalize


class ProspectQueue:
    def __init__(self, store):
        self.store = store

    def buckets(self) -> dict:
        allowed, manual, blocked = [], [], []
        for p in self.store.prospects():
            record = normalize(
                self.store.get_permissions(p["prospect_id"]))
            entry = {"prospect": p, "record": record}
            if record["blocked"]:
                blocked.append(entry)
            elif record["permission"] == "allowed":
                allowed.append(entry)
            else:
                manual.append(entry)
        return {"allowed": allowed, "manual": manual, "blocked": blocked}

    def allowed(self) -> list:
        """People the human has cleared for autonomous contact, best
        candidate first."""
        return self.buckets()["allowed"]

    def next(self) -> dict | None:
        """The single next person to work, or None if the allowed bucket
        is empty. One person at a time."""
        bucket = self.allowed()
        return bucket[0] if bucket else None

    def summary(self) -> dict:
        b = self.buckets()
        return {k: len(v) for k, v in b.items()}
