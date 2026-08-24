"""Campaign lifecycle: create from YAML config, assign eligible prospects,
select the next outbound batch. All decisions are deterministic rules - the
LLM is never asked who to contact or in what order.
"""


from ..timeutil import iso as _now_iso
import uuid
from datetime import datetime, timezone

from ..utils import campaign_config
from .eligibility import is_eligible, eligible_prospects
from .state_machine import transition

MESSAGE_STATES = {"CAMPAIGN_ASSIGNED", "MESSAGE_PENDING"}


def create_campaign(store, cfg: dict, campaign_id: str = None,
                    status: str = "draft") -> dict:
    """Create (or refresh) a campaign row from a config dict."""
    campaign_id = campaign_id or cfg.get("_file") or f"campaign_{uuid.uuid4().hex[:6]}"
    created = store.get_campaign(campaign_id)
    campaign = {
        "campaign_id": campaign_id,
        "name": cfg.get("campaign", {}).get("name", campaign_id),
        "objective": cfg.get("campaign", {}).get("objective", "activated_signup"),
        "strategy": cfg.get("strategy", {}).get("primary", "conversation_first"),
        "status": (created or {}).get("status", status),
        "config": cfg,
        "created_at": (created or {}).get("created_at")
        or _now_iso(),
    }
    store.save_campaign(campaign)
    return campaign


def sync_campaigns(store) -> list:
    """Create/refresh a campaign row for every YAML in config/campaigns/."""
    from ..utils import list_campaign_configs
    out = []
    for cfg in list_campaign_configs():
        out.append(create_campaign(store, cfg))
    return out


def _priority(prospect: dict) -> float:
    fit = prospect.get("qualification_fit")
    if fit is None:
        fit = prospect.get("total_score", 0)
    try:
        fit = float(fit)
    except (TypeError, ValueError):
        fit = 0.0
    conf = prospect.get("qualification_confidence") or 0.0
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0.0
    pain = prospect.get("pain_state")
    pain_bonus = 10.0 if pain == "demonstrated" else (
        5.0 if pain == "plausible" else 0.0)
    return round(fit * 0.7 + conf * 100 * 0.3 + pain_bonus, 1)


def eligible_for_campaign(store, campaign: dict, prospects: list,
                          min_priority: float = 40.0) -> list:
    """Eligible + priority-ranked candidates for a campaign, never assigned
    already. Returns prospect dicts with `outreach_priority` set."""
    active_ids = {cp["prospect_id"]
                  for cp in store.campaign_prospects(campaign["campaign_id"])}
    out = []
    for p in eligible_prospects(store, campaign, prospects):
        if p["prospect_id"] in active_ids:
            continue
        prio = _priority(p)
        if prio < min_priority:
            continue
        p["outreach_priority"] = prio
        out.append(p)
    out.sort(key=lambda x: x["outreach_priority"], reverse=True)
    return out


def assign_prospects(store, campaign: dict, prospects: list,
                     strategy: str = None) -> list:
    """Move candidates into the campaign as CAMPAIGN_ASSIGNED rows."""
    strategy = strategy or campaign.get("strategy")
    assigned = []
    for p in prospects:
        cp = {
            "campaign_id": campaign["campaign_id"],
            "prospect_id": p["prospect_id"],
            "status": "CAMPAIGN_ASSIGNED",
            "priority": p.get("outreach_priority", _priority(p)),
            "assigned_strategy": strategy,
            "entered_at": _now_iso(),
            "last_action": None,
        }
        store.assign_campaign_prospect(cp)
        transition(store, cp, "CAMPAIGN_ASSIGNED", event="assigned")
        assigned.append(cp)
    return assigned


def next_batch(store, campaign: dict, limit: int = 0) -> list:
    """Pick the next `limit` CAMPAIGN_ASSIGNED prospects and move them to
    MESSAGE_PENDING. Deterministic: highest priority first. limit=0 means all."""
    cps = store.campaign_prospects(campaign["campaign_id"],
                                   status="CAMPAIGN_ASSIGNED")
    cps.sort(key=lambda x: (x.get("priority") or 0), reverse=True)
    if limit > 0:
        cps = cps[:limit]
    batch = []
    for cp in cps:
        try:
            moved = transition(store, cp, "MESSAGE_PENDING", event="batch_start")
        except ValueError:
            continue
        batch.append(moved)
    return batch


def assign_batch(store, campaign: dict, prospects: list,
                 limit: int = None) -> dict:
    """One-shot helper: filter, rank, assign, and start message generation for
    up to `limit` candidates. Returns summary counts."""
    cfg = campaign_config(campaign)
    limit = limit or int(cfg.get("limits", {}).get(
        "max_new_prospects_per_batch", 10))
    min_prio = float(cfg.get("limits", {}).get("min_priority", 40))
    candidates = eligible_for_campaign(store, campaign, prospects, min_prio)[:limit]
    assigned = assign_prospects(store, campaign, candidates)
    started = next_batch(store, campaign, limit=len(assigned))
    return {
        "campaign_id": campaign["campaign_id"],
        "eligible": len(candidates),
        "assigned": len(assigned),
        "started": len(started),
    }
