"""Deduplicate connections across multiple matching strategies, keeping a merge log."""

from .normalizer import normalize_name, normalize_company, normalize_position, normalize_url
from .parsers import ConnectionRecord


def _completeness(rec: ConnectionRecord) -> int:
    score = 0
    for v in (rec.email, rec.position, rec.company, rec.connected_on, rec.url):
        if v:
            score += 1
    return score


def _keys(rec: ConnectionRecord) -> list:
    """Candidate identity keys in priority order (url > name+company > name+position+date)."""
    keys = []
    url = normalize_url(rec.url)
    if url:
        keys.append(("url", url))
    nname = normalize_name(rec.full_name)
    ncomp = normalize_company(rec.company)
    if nname and ncomp:
        keys.append(("name_company", nname, ncomp))
    npos = normalize_position(rec.position)
    ndate = (rec.connected_on or "").strip()
    if nname and (npos or ndate):
        keys.append(("name_pos", nname, npos, ndate))
    return keys


def _reason(rec: ConnectionRecord, key: tuple) -> str:
    if key[0] == "url":
        return f"same profile URL {rec.url}"
    if key[0] == "name_company":
        return (f"same normalized name+company "
                f"({normalize_name(rec.full_name)} / {normalize_company(rec.company)})")
    return "same normalized name+position+date"


_STRATEGY_NAMES = {"url": "exact_url", "name_company": "name_company",
                   "name_pos": "name_position_date"}


def deduplicate(records: list) -> tuple:
    """Return (unique_prospects, merge_log, stats).

    Every record is registered under ALL its identity keys, so a later record
    with a different URL but the same normalized name+company (or
    name+position+date) still merges into the already-seen person.
    """
    stats = {"raw_records": len(records), "duplicates_removed": 0, "strategies": {}}
    merge_log = []

    def mark(rec, absorbed, strategy, reason):
        stats["strategies"][strategy] = stats["strategies"].get(strategy, 0) + 1
        stats["duplicates_removed"] += 1
        merge_log.append({
            "kept": {"name": rec.full_name, "url": rec.url},
            "merged": {"name": absorbed.full_name, "url": absorbed.url},
            "strategy": strategy,
            "reason": reason,
        })

    def register(rec):
        for key in _keys(rec):
            canonical[key] = rec
            used_by[key] = _STRATEGY_NAMES[key[0]]

    canonical = {}  # (strategy_key) -> ConnectionRecord
    used_by = {}    # (strategy_key) -> strategy name

    for rec in records:
        merged_into = None
        chosen_key = None
        chosen_strategy = None
        chosen_reason = ""
        for key in _keys(rec):
            if key in canonical:
                merged_into = canonical[key]
                chosen_key, chosen_strategy = key, used_by[key]
                chosen_reason = _reason(rec, key)
                break

        if merged_into is None:
            register(rec)
        else:
            # Keep the richer record as canonical.
            if _completeness(rec) > _completeness(merged_into):
                mark(rec, merged_into, chosen_strategy, chosen_reason)
                merged_into.__dict__.update(rec.__dict__)
                register(merged_into)
            else:
                mark(merged_into, rec, chosen_strategy, chosen_reason)

    unique_records = list(canonical.values())
    unique_records = list({id(r): r for r in unique_records}.values())
    unique_records.sort(key=lambda r: (r.last_name.lower(), r.first_name.lower()))

    report = {
        "raw_records": stats["raw_records"],
        "unique_prospects": len(unique_records),
        "duplicates_removed": stats["duplicates_removed"],
        "strategies_used": stats["strategies"],
        "merge_log": merge_log,
    }
    return unique_records, merge_log, report
