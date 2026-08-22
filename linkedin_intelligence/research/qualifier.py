"""LLM qualification: structured evidence in, structured JSON out.

Every qualification carries:
  - research_mode: "offline" (export-derived only) vs "live_browser"
    (browser research performed) - downstream logic MUST treat these differently
  - method: "llm" or "rules"
  - confidence: 0-1, and "unknown" is a legitimate outcome. The model must not
    infer pain, intent, usage or interest without evidence.

The model never sees the CSV or the whole DB - only the compact prospect
record + observed evidence.
"""

import json
import re
import requests

from ..utils import load_icp

NEXT_ACTIONS = ("SKIP", "RESEARCH_MORE", "READY_FOR_HUMAN_REVIEW",
                "READY_FOR_OUTREACH", "FOLLOW_UP", "DO_NOT_CONTACT")
UNKNOWN_OK = ("unknown", None)

APPROVAL_ACTIONS = ("READY_FOR_HUMAN_REVIEW", "READY_FOR_OUTREACH")


def build_prompt(prospect: dict, evidence: list) -> str:
    icp = load_icp()
    product = icp.get("product", {})
    known = prospect.get("evidence", [])
    lines = [
        "PROSPECT",
        prospect.get("full_name", ""),
        prospect.get("raw_position") or "unknown role",
        prospect.get("current_company") or "unknown company",
        "",
        "KNOWN FACTS:",
    ]
    lines += [f"- {k}" for k in known] or ["- (none)"]
    lines += ["", "OBSERVED EVIDENCE:"]
    for e in evidence:
        src = f"{e.get('source')}/{e.get('collector')}"
        conf = e.get('confidence', 'n/a')
        lines.append(f"- {e['claim']} (source: {src}, confidence: {conf})")
    lines += [
        "",
        "PRODUCT:",
        f"{product.get('name', 'our product')} - {product.get('summary', '')}",
        "Relevant problems we target: " + ", ".join(product.get("problems", [])),
        "",
        "SCORING RULES:",
        "1. fit_score (0-100): How well does this person match our ideal customer?",
        "   - 80-100: Decision-maker, relevant role, target company type",
        "   - 50-79: Relevant role at a company that could use our product",
        "   - 20-49: tangentially related",
        "   - 0-19: Student, intern, completely unrelated",
        "2. Set problem_fit_score to your best estimate (0-100) based on role/company match.",
        "   Only set null if ZERO information is available.",
        "3. confidence (0.0-1.0): How confident are you in this assessment?",
        "   - 0.8+: Clear profile with visible role/company",
        "   - 0.5-0.8: Partial info, some assumptions made",
        "   - Below 0.5: Very little data",
        "4. NEVER set recommended_next_action to SKIP unless the person is clearly",
        "   a student/intern or completely irrelevant to our product.",
    ]
    lines += [
        "",
        "Return ONLY a JSON object with exactly these keys:",
        '{"fit_score": 0-100, "problem_fit_score": 0-100, '
        '"confidence": 0.0-1.0, "reason": "...", "evidence_used": [...], '
        '"uncertainties": [...], "recommended_next_action": one of '
        + ", ".join(NEXT_ACTIONS) + "}",
    ]
    return "\n".join(lines)


def parse_qualification(text: str) -> dict:
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _to_num(value, lo, hi, default):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _sanitize(q: dict) -> dict:
    action = str(q.get("recommended_next_action", "")).strip().upper()
    if action not in NEXT_ACTIONS:
        action = "RESEARCH_MORE"
    fit = int(round(_to_num(q.get("fit_score"), 0, 100, 50)))
    pfs = q.get("problem_fit_score")
    problem_fit = None
    if pfs not in (None, "null", ""):
        problem_fit = int(round(_to_num(pfs, 0, 100, 50)))
    return {
        "fit_score": fit,
        "problem_fit_score": problem_fit,
        "confidence": round(_to_num(q.get("confidence"), 0.0, 1.0, 0.5), 2),
        "reason": str(q.get("reason", ""))[:500],
        "evidence_used": [str(x)[:200] for x in q.get("evidence_used", [])],
        "uncertainties": [str(x)[:200] for x in q.get("uncertainties", [])],
        "recommended_next_action": action,
    }


def _rule_fallback(prospect: dict) -> dict:
    """Deterministic fallback when no LLM is reachable.

    Uses only export-derived data, so research_mode stays "offline" and the
    confidence is explicitly low so downstream logic does not treat it as
    observed evidence.
    """
    score = prospect.get("total_score", 0)
    intent = prospect.get("commercial_intent", "unknown")
    icp = load_icp()
    if intent == "declined" or prospect.get("status") in (
            "do_not_contact", "not_relevant", "already_customer", "declined"):
        action = "DO_NOT_CONTACT"
    else:
        action = "RESEARCH_MORE"
    return {
        "fit_score": int(round(score)),
        "problem_fit_score": None,
        "confidence": 0.4,
        "reason": "Rule fallback from Phase 1 score (LLM unavailable). "
                  "Problem fit not assessed - unknown.",
        "evidence_used": prospect.get("evidence", [])[:5],
        "uncertainties": ["No browser research performed",
                          "Problem fit unknown"],
        "recommended_next_action": action,
    }


def validate_qualification(prospect: dict, q: dict, icp: dict = None) -> dict:
    """Deterministic semantic checks: the model's own numbers must agree with
    its recommended action, and with the config thresholds.

    Contradictions are recorded (never silently dropped) and unsupported
    approval actions are downgraded to RESEARCH_MORE. Hard negatives
    (declined / do-not-contact) are never overridden here.
    """
    icp = icp or load_icp()
    qual_cfg = icp.get("qualification", {})
    ready_review = float(qual_cfg.get("ready_for_review", 70))
    research_more = float(qual_cfg.get("research_more", 50))
    min_conf = float(qual_cfg.get("min_confidence_for_ready", 0.5))

    q = dict(q)
    q.setdefault("contradictions", [])
    action = q.get("recommended_next_action", "RESEARCH_MORE")

    def _flag(msg):
        if msg not in q["contradictions"]:
            q["contradictions"].append(msg)

    fit = q.get("fit_score")
    conf = q.get("confidence") or 0.0
    pfs = q.get("problem_fit_score")
    signal = prospect.get("commercial_intent", "unknown")

    def _flag(msg):
        if msg not in q["contradictions"]:
            q["contradictions"].append(msg)

    if action in APPROVAL_ACTIONS and fit is not None and fit < ready_review:
        _flag(f"fit {fit} < ready threshold {ready_review} but action {action}")
        action = "RESEARCH_MORE"
    if action in APPROVAL_ACTIONS and conf < min_conf:
        _flag(f"confidence {conf} below {min_conf} for {action}")
        action = "RESEARCH_MORE"
    if action == "SKIP" and fit is not None and fit >= research_more:
        _flag(f"fit {fit} >= {research_more} but action SKIP")
    if action == "DO_NOT_CONTACT" and signal != "declined":
        _flag("DO_NOT_CONTACT without declined signal")
    if action == "RESEARCH_MORE" and fit is not None and pfs is not None \
            and fit >= ready_review and pfs >= ready_review:
        _flag(f"fit {fit} and problem fit {pfs} both strong but action RESEARCH_MORE")

    q["recommended_next_action"] = action
    return q


def qualify(store, prospect: dict, model: str = "gemma4:31b-cloud",
            base_url: str = "http://localhost:11434",
            evidence: list = None, research_mode: str = "offline",
            timeout: int = 600) -> dict:
    """Qualify one prospect. research_mode must reflect how evidence was gathered.

    timeout is generous because local Ollama may run CPU-only (a 12B model can
    take 10+ minutes per qualification on CPU).
    """
    evidence = evidence if evidence is not None else evidence_block(store, prospect)
    prompt = build_prompt(prospect, evidence)

    payload = {
        "model": model,
        # Qwen3/GPT-OSS-class models default to "thinking" mode, which burns the
        # whole generation budget on a reasoning trace and can return empty
        # content. Disable it explicitly - the flag is only honored at the TOP
        # level of the request, not inside `options`.
        "think": False,
        "messages": [
            {"role": "system",
             "content": "You are a conservative sales-qualification model. "
                        "Output only valid JSON, no prose. Unknown is a valid "
                        "answer - never invent evidence."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.1},
    }
    try:
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
        if resp.status_code != 200:
            q = _rule_fallback(prospect)
            q["research_mode"] = research_mode
            q["method"] = "rules"
            return q
        data = resp.json()
        text = data.get("message", {}).get("content", "")
        q = _sanitize(parse_qualification(text))
        q = validate_qualification(prospect, q)
        q["model"] = model
        q["research_mode"] = research_mode
        q["method"] = "llm"
        return q
    except requests.exceptions.RequestException:
        q = _rule_fallback(prospect)
        q["research_mode"] = research_mode
        q["method"] = "rules"
        return q
