"""Deterministic scoring: hard exclusions first, then ICP fit + total score.

Dimensions stay SEPARATE and are never collapsed into one opaque number:
  role_fit, company_fit, relationship, engagement, problem_fit, buying_signal.
problem_fit_score is UNKNOWN (None) until Phase 2 evidence/qualification
produces it; the system never manufactures problem fit from a title alone.
"""

import uuid

from ..utils import load_icp, collapse_ws
from .normalizer import normalize_company, normalize_position
from .relationship import (relationship_components, engagement_score,
                           buying_signal, match_conversations,
                           match_invitations)


def build_prospect(rec, convos, invitations, config, my_schools=None):
    """Turn one deduplicated ConnectionRecord into a full Prospect dict."""
    my_schools = my_schools or []
    matched_convos = match_conversations(convos, rec.url, rec.full_name)
    matched_invs = match_invitations(rec, invitations)

    icp = config["icp"]
    tax = config["taxonomy"]

    role_cat, role_pat = tax["classify_position"](normalize_position(rec.position))
    comp_type, comp_pat = tax["classify_company"](normalize_company(rec.company))
    role_cat = role_cat or "unknown"
    comp_type = comp_type or "unknown"

    rel = relationship_components(rec, matched_convos, matched_invs,
                                  shared_school=False)
    eng, eng_parts = engagement_score(matched_convos, config)
    bs_score, bs_intent, bs_conf = buying_signal(matched_convos, config)

    role_weight = icp["target_roles"].get(role_cat, icp["target_roles"].get("unknown", 0.25))
    comp_weight = icp["target_company_types"].get(comp_type, icp["target_company_types"].get("unknown", 0.3))

    role_score = round(role_weight * 100, 1)
    company_score = round(comp_weight * 100, 1)

    rel_w = icp["weights"]["relevance"]
    relevance_score = round(rel_w["role"] * role_score + rel_w["company"] * company_score, 1)

    # Initial hypothesis only - problem fit is UNKNOWN until evidence exists.
    affinity = icp.get("problem_affinity", {}).get(role_cat, 0.2)
    problem_fit_score = None
    problem_fit_confidence = None

    total_score = _compute_total(icp, relevance_score, rel["score"], eng,
                                 bs_score, None)

    evidence = []
    if rec.position:
        evidence.append(f"Role: {rec.position}")
    if role_cat != "unknown":
        evidence.append(f"Role category: {role_cat}")
    if rec.company:
        evidence.append(f"Company: {rec.company}")
    if comp_type != "unknown":
        evidence.append(f"Company type: {comp_type}")
    evidence.append("Existing first-degree connection")
    if matched_convos:
        evidence.append(f"Prior conversation ({len(matched_convos)} thread(s))")
        intent = matched_convos[0]["intelligence"]["commercial_intent"]
        if intent in ("explicit", "possible"):
            evidence.append(f"Commercial intent (keyword hint): {intent}")
    if rec.connected_on:
        evidence.append(f"Connected: {rec.connected_on}")

    conversation_intelligence = None
    if matched_convos:
        best = max(matched_convos,
                   key=lambda c: (c["intelligence"]["commercial_intent"] == "explicit",
                                  c["intelligence"]["confidence"]))
        conversation_intelligence = dict(best["intelligence"])
        conversation_intelligence["last_date"] = best["last_date"]

    return {
        "prospect_id": "p_" + uuid.uuid4().hex[:10],
        "full_name": rec.full_name,
        "first_name": rec.first_name,
        "last_name": rec.last_name,
        "linkedin_url": rec.url,
        "email": rec.email or None,
        "current_company": rec.company or None,
        "normalized_company": normalize_company(rec.company) or None,
        "company_type": comp_type,
        "raw_position": rec.position or None,
        "normalized_position": normalize_position(rec.position) or None,
        "role_category": role_cat,
        "role_pattern": role_pat,
        "connection_status": "1st_degree",
        "connected_date": rec.connected_on or None,
        "invitation_history": matched_invs,
        "conversation_history": [collapse_ws(c["topic"]) for c in matched_convos],
        "conversation_ids": [c["conversation_id"] for c in matched_convos],
        "conversation_count": len(matched_convos),
        "conversation_intelligence": conversation_intelligence,
        # --- separate score dimensions ---
        "role_fit": role_score,
        "company_fit": company_score,
        "relationship_score": rel["score"],
        "relationship_components": rel["components"],
        "engagement_score": eng,
        "engagement_components": eng_parts,
        "problem_fit_score": problem_fit_score,
        "problem_fit_confidence": problem_fit_confidence,
        "problem_affinity_hint": round(affinity * 100, 1),
        "buying_signal_score": bs_score,
        "commercial_intent": bs_intent,
        "buying_signal_confidence": bs_conf,
        "relevance_score": relevance_score,
        "total_score": total_score,
        "evidence": evidence,
        "segments": [],
        "selection_reasons": [],
        "research_mode": None,
        "status": "new",
        "last_contacted": max((c["last_date"] for c in matched_convos), default=None),
        "next_action": None,
        "source": "connections_export",
    }


def _compute_total(icp, relevance, relationship, engagement, buying_signal,
                   problem_fit):
    """Weighted ordering score. Dimensions remain separately stored."""
    w = icp["weights"]["total"]
    total = (w["relevance"] * relevance
             + w["relationship"] * relationship
             + w["engagement"] * engagement
             + w["buying_signal"] * (buying_signal or 0))
    if problem_fit is not None:
        blend = icp["weights"].get("problem_fit_blend_when_known", 0.25)
        total = (1 - blend) * total + blend * problem_fit
    return round(total, 1)


def recompute_total(prospect, icp):
    """Recompute ordering score after problem_fit becomes known."""
    prospect["total_score"] = _compute_total(
        icp, prospect.get("relevance_score", 0),
        prospect.get("relationship_score", 0),
        prospect.get("engagement_score", 0),
        prospect.get("buying_signal_score", 0),
        prospect.get("problem_fit_score"))
    return prospect["total_score"]


def apply_hard_exclusions(prospects, config):
    """Hard rules in code - filtered out before scoring is even meaningful."""
    icp = config["icp"]
    exclusions = icp.get("hard_exclusions", {})
    ignore_companies = {normalize_company(c) for c in exclusions.get("ignore_companies", [])}
    ignore_roles = set(exclusions.get("ignore_role_categories", []))

    out = []
    excluded = []
    for p in prospects:
        reasons = []
        if p["normalized_company"] in ignore_companies:
            reasons.append("company on ignore list")
        if p["role_category"] in ignore_roles:
            reasons.append("role category on ignore list")
        if p["role_category"] == "unknown" and not p["raw_position"]:
            reasons.append("no position available")
        if reasons:
            excluded.append({"prospect_id": p["prospect_id"],
                             "name": p["full_name"], "reasons": reasons})
        else:
            out.append(p)
    return out, excluded


def score(prospects, config) -> dict:
    """Run hard exclusions then scoring; return (kept, excluded)."""
    kept, excluded = apply_hard_exclusions(prospects, config)
    icp = config["icp"]
    hi = icp.get("high_relevance_threshold", 55)
    lo = icp.get("potential_relevance_threshold", 40)
    for p in kept:
        if p["total_score"] >= hi:
            p["segment_band"] = "high_relevance"
        elif p["total_score"] >= lo:
            p["segment_band"] = "potential_relevance"
        else:
            p["segment_band"] = "low_relevance"
    return kept, excluded
