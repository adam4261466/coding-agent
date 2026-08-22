"""Map normalized positions/companies into the controlled taxonomy.

Longest-pattern-match: for every pattern in every category, if the pattern
appears as a substring of the normalized value, keep the longest match; ties
go to the earlier category in taxonomy.yaml. Short patterns (<4 chars, e.g.
"ai", "it", "sa") only match on whole-word boundaries to avoid false positives.
Unknown -> None (stored as 'unknown' downstream).
"""

import re

from ..utils import load_taxonomy


def _matches(pat: str, text: str) -> bool:
    if len(pat) < 4:
        return re.search(r"\b" + re.escape(pat) + r"\b", text) is not None
    return pat in text


def _taxonomy():
    return load_taxonomy()


def classify_position(normalized_position: str) -> tuple:
    """Return (role_category, matched_pattern) or (None, None)."""
    if not normalized_position:
        return None, None
    tax = _taxonomy()
    best_len = -1
    best_cat = None
    best_pat = None
    for cat, patterns in tax.get("roles", {}).items():
        for pat in patterns:
            p = pat.strip().lower()
            if p and _matches(p, normalized_position) and len(p) > best_len:
                best_len = len(p)
                best_cat = cat
                best_pat = p
    return best_cat, best_pat


def classify_company(normalized_company: str) -> tuple:
    """Return (company_type, matched_pattern) or (None, None)."""
    if not normalized_company:
        return None, None
    tax = _taxonomy()
    best_len = -1
    best_type = None
    best_pat = None
    for ctype, patterns in tax.get("company_types", {}).items():
        for pat in patterns:
            p = pat.strip().lower()
            if p and _matches(p, normalized_company) and len(p) > best_len:
                best_len = len(p)
                best_type = ctype
                best_pat = p
    return best_type, best_pat
