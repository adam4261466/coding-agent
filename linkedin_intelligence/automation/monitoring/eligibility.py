"""Eligibility sweeps: find prospects ready for the next action.

Runs periodically inside the agent loop to discover prospects that have
become eligible for outreach (follow-ups due, conversations to handle, etc.).
"""

from datetime import datetime, timezone

from ...timeutil import now as _now


def sweep_follow_ups(store, campaigns: list) -> list:
    """Find all prospects eligible for follow-up across all active campaigns."""
    from ...outreach.sequence import due_follow_ups
    now = _now()
    results = []
    for campaign in campaigns:
        if campaign.get("status") not in ("active", "synced"):
            continue
        due = due_follow_ups(store, campaign, now)
        for cp in due:
            results.append({
                "prospect_id": cp["prospect_id"],
                "campaign_id": campaign["campaign_id"],
                "reason": "follow_up_due",
                "campaign": campaign,
                "cp": cp,
            })
    return results


def sweep_conversations(store, campaigns: list) -> list:
    """Find prospects awaiting a response that might need attention."""
    results = []
    for campaign in campaigns:
        if campaign.get("status") not in ("active", "synced"):
            continue
        cps = store.campaign_prospects(
            campaign_id=campaign["campaign_id"], status="AWAITING_RESPONSE")
        for cp in cps:
            results.append({
                "prospect_id": cp["prospect_id"],
                "campaign_id": campaign["campaign_id"],
                "reason": "awaiting_response",
                "campaign": campaign,
                "cp": cp,
            })
    return results


def sweep_message_pending(store, campaigns: list) -> list:
    """Find prospects ready for message production."""
    results = []
    for campaign in campaigns:
        if campaign.get("status") not in ("active", "synced"):
            continue
        cps = store.campaign_prospects(
            campaign_id=campaign["campaign_id"], status="MESSAGE_PENDING")
        for cp in cps:
            results.append({
                "prospect_id": cp["prospect_id"],
                "campaign_id": campaign["campaign_id"],
                "reason": "message_pending",
                "campaign": campaign,
                "cp": cp,
            })
    return results


def sweep_message_review(store, campaigns: list) -> list:
    """Find prospects with generated messages awaiting approval."""
    results = []
    for campaign in campaigns:
        if campaign.get("status") not in ("active", "synced"):
            continue
        cps = store.campaign_prospects(
            campaign_id=campaign["campaign_id"], status="MESSAGE_REVIEW")
        for cp in cps:
            results.append({
                "prospect_id": cp["prospect_id"],
                "campaign_id": campaign["campaign_id"],
                "reason": "message_review",
                "campaign": campaign,
                "cp": cp,
            })
    return results


def sweep_approved_to_send(store, campaigns: list) -> list:
    """Find prospects with approved messages ready to send."""
    results = []
    for campaign in campaigns:
        if campaign.get("status") not in ("active", "synced"):
            continue
        cps = store.campaign_prospects(
            campaign_id=campaign["campaign_id"],
            status="APPROVED_TO_SEND")
        for cp in cps:
            results.append({
                "prospect_id": cp["prospect_id"],
                "campaign_id": campaign["campaign_id"],
                "reason": "approved_to_send",
                "campaign": campaign,
                "cp": cp,
            })
    return results


def sweep_all(store) -> list:
    """Run all eligibility sweeps and return prioritized work items."""
    campaigns = store.campaigns(status="active") + store.campaigns(status="synced")
    items = []
    items.extend(sweep_follow_ups(store, campaigns))
    items.extend(sweep_conversations(store, campaigns))
    items.extend(sweep_message_pending(store, campaigns))
    items.extend(sweep_message_review(store, campaigns))
    items.extend(sweep_approved_to_send(store, campaigns))
    return items
