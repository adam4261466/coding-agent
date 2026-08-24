"""Project-wide clock: every timestamp is LOCAL time, tz-aware ISO 8601.

Stored strings look like 2026-08-24T00:45:51.217650+01:00 so the database,
the rate limiter and the console logs always show the same wall clock the
operator sees. Legacy rows written as naive/UTC are handled by parse().
"""

from datetime import datetime, timezone


def now() -> datetime:
    """Current LOCAL time (tz-aware)."""
    return datetime.now().astimezone()


def iso() -> str:
    """Current LOCAL time as an ISO string - the only stamping API."""
    return now().isoformat()


def parse(value) -> datetime:
    """Tolerant ISO parse. Aware values pass through untouched; naive
    values are treated as LEGACY UTC and converted to local."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()
