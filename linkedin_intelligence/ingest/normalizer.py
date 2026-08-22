"""Normalization: names, companies, positions. Raw values are always preserved."""

import re

from ..utils import ascii_fold, collapse_ws


def normalize_name(text: str) -> str:
    """'AHMED AMINE' / 'Ahmed Amine' / 'Ahmed Amine EZ-ZAHERY' -> 'ahmed amine ez zahery'."""
    return ascii_fold(text)


def normalize_company(text: str) -> str:
    """'OpenAI ' / 'OPENAI' -> 'openai'."""
    if not text:
        return ""
    value = collapse_ws(str(text)).strip("\"' ")
    value = re.sub(r"[.,;:\s]+$", "", value)
    return value.lower()


def normalize_position(text: str) -> str:
    """'Software Engineering intern' -> 'software engineering intern'."""
    return ascii_fold(text)


def normalize_url(text: str) -> str:
    value = (text or "").strip().rstrip("/").lower()
    if value.startswith("https://www.linkedin.com/in/"):
        value = value[len("https://www.linkedin.com/in/"):]
    elif value.startswith("http://www.linkedin.com/in/"):
        value = value[len("http://www.linkedin.com/in/"):]
    elif value.startswith("linkedin.com/in/"):
        value = value[len("linkedin.com/in/"):]
    return value


def normalize_school(text: str) -> str:
    return ascii_fold(text)
