"""Pain-state semantics: separate "not researched yet" from "no pain".

The pipeline never conflates these two states - that conflation is exactly
what made the raw threshold look like it predicted product fit. Pain state is
assigned deterministically from evidence state:

  declined          hard negative (commercial_intent declined / do-not-contact)
  demonstrated      problem-fit score >= demonstrated threshold
  plausible         problem-fit assessed but below demonstrated threshold
  no_pain_evidence  researched, but no problem-fit evidence surfaced
  not_researched    no research / no qualification yet (NOT "no pain")

Only the LLM's assessed problem_fit_score or an explicit conversation pain
signal can move a prospect out of "not_researched".
"""

PAIN_STATES = ("declined", "demonstrated", "plausible", "no_pain_evidence",
               "not_researched")

NEGATIVE_STATUSES = {"do_not_contact", "not_relevant", "already_customer",
                     "declined", "skipped"}


def pain_state(prospect: dict, qualification: dict = None,
               demonstrated_threshold: float = 60) -> str:
    """One deterministic pain state per prospect. Order matters: hard negatives
    win, then evidence, then "no_pain_evidence" only after research."""
    if prospect.get("commercial_intent") == "declined" or \
            prospect.get("status") in NEGATIVE_STATUSES:
        return "declined"

    pfs = prospect.get("problem_fit_score")
    if pfs is not None:
        return "demonstrated" if pfs >= demonstrated_threshold else "plausible"

    ci = prospect.get("conversation_intelligence") or {}
    if ci.get("pain_signal") == "explicit":
        return "demonstrated"
    if ci.get("pain_signal") in ("possible", "hint"):
        return "plausible"

    researched = prospect.get("research_mode") == "live_browser" or \
        qualification is not None or \
        (prospect.get("qualification_fit") is not None)
    if researched:
        return "no_pain_evidence"
    return "not_researched"
