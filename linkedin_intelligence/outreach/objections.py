"""Objection taxonomy and response policy.

An objection is NEVER argued with automatically. The flow is:
  objection -> understand (classify) -> prepare a response draft
  -> human approval before anything is sent.
The LLM may propose a draft; a human approves every reply that goes out.
"""

OBJECTION_CATEGORIES = [
    "price", "no_need", "already_have_solution", "complexity",
    "trust", "timing", "not_understood", "competitor", "unknown",
]


def objection_category(label: str) -> str:
    label = (label or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    if label in OBJECTION_CATEGORIES:
        return label
    return "unknown"


def guidance(category: str) -> str:
    """Deterministic handling guidance for the human reviewing a draft reply.
    This is policy, not LLM output."""
    return {
        "price": "Do not discount or negotiate in the first reply. Restate value "
                 "in their terms, ask what budget assumption they have.",
        "no_need": "Ask what they currently do for this problem before assuming. "
                   "Do not argue that they DO have the need.",
        "already_have_solution": "Ask what they use and what works/fails. Offer a "
                                 "concrete comparison only if evidence supports it.",
        "complexity": "Ask which part seems complex. Offer a short, concrete "
                      "walkthrough - do not dump features.",
        "trust": "Reference proof that is real and verifiable. Never invent "
                 "customers, numbers, or claims.",
        "timing": "Ask when the decision cycle reopens. Never pressure.",
        "not_understood": "Re-explain in plainer terms, shorter, with one concrete "
                          "example from their own context.",
        "competitor": "Do not disparage the competitor. Ask what they like about "
                      "it and where it falls short for their specific use.",
        "unknown": "Ask a clarifying question. Do not guess the objection.",
    }.get(category, "Ask a clarifying question. Do not guess the objection.")
