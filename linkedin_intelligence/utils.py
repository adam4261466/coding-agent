"""Shared paths, YAML loading, text helpers for the linkedin_intelligence package."""

import html
import json
import os
import re
import unicodedata

import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "data")
INTELLIGENCE_DIR = os.path.join(DATA_DIR, "intelligence")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
DB_DIR = os.path.join(BASE_DIR, "db")
DB_PATH = os.path.join(DB_DIR, "linkedin_intelligence.sqlite")
CONFIG_DIR = os.path.join(BASE_DIR, "config")

# Where the untouched LinkedIn export lives. The pipeline never writes here.
DEFAULT_SOURCE_DIR = os.path.join(ROOT_DIR, "linkedin_export")

PY_YAML = object()


def ensure_dirs():
    for d in (DATA_DIR, INTELLIGENCE_DIR, REPORTS_DIR, DB_DIR):
        os.makedirs(d, exist_ok=True)


def load_yaml(name: str) -> dict:
    path = os.path.join(CONFIG_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_icp() -> dict:
    return load_yaml("icp.yaml")


def load_taxonomy() -> dict:
    return load_yaml("taxonomy.yaml")


def load_privacy() -> dict:
    return load_yaml("privacy.yaml")


def load_segments() -> dict:
    return load_yaml("segments.yaml")


def load_product_events() -> dict:
    return load_yaml("product_events.yaml")


def load_optimization() -> dict:
    return load_yaml("optimization.yaml")


def load_campaign_config(name: str) -> dict:
    """Load ONE campaign config from config/campaigns/<name>.yaml."""
    import os
    path = os.path.join(CONFIG_DIR, "campaigns", f"{name}.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def list_campaign_configs() -> list:
    """Return the config dicts for every campaign yaml in config/campaigns/."""
    import os
    out = []
    d = os.path.join(CONFIG_DIR, "campaigns")
    if not os.path.isdir(d):
        return out
    for fname in sorted(os.listdir(d)):
        if fname.endswith(".yaml") or fname.endswith(".yml"):
            cfg = load_campaign_config(os.path.splitext(fname)[0])
            cfg["_file"] = os.path.splitext(fname)[0]
            out.append(cfg)
    return out


def campaign_config(campaign: dict) -> dict:
    """Effective config for a campaign dict: the stored `config` blob merged
    with any top-level keys that are not meta fields."""
    cfg = dict(campaign.get("config") or {})
    meta = {"config", "campaign_id", "name", "objective", "strategy",
            "status", "created_at"}
    for k, v in campaign.items():
        if k not in meta:
            cfg.setdefault(k, v)
    return cfg


def save_json(obj, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def save_jsonl(rows: list, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def ascii_fold(text: str) -> str:
    """Lowercase, decompose latin accents, keep the rest (e.g. Arabic) intact."""
    if not text:
        return ""
    text = collapse_ws(str(text)).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[\-–—_,.]+", " ", text)
    return collapse_ws(text)


def own_identity(profile_rows: list) -> dict:
    """Best-effort identity from Profile.csv so message direction can be derived."""
    for r in profile_rows:
        first = collapse_ws(r.get("First Name", ""))
        last = collapse_ws(r.get("Last Name", ""))
        if first or last:
            return {
                "first_name": first,
                "last_name": last,
                "full_name": f"{first} {last}".strip(),
            }
    return {"first_name": "", "last_name": "", "full_name": ""}
