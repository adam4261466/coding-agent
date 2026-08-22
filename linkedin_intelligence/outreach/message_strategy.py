"""The three outreach message strategies.

A strategy decides HOW the message opens and what it leads with. The
generator renders a concrete message from a strategy. Strategies are fixed
here and referenced by name in campaign configs - the LLM picks wording, the
config picks strategy.
"""

STRATEGIES = {
    "conversation_first": {
        "label": "Conversation First",
        "description": "Warm, low-pressure opener about the prospect's own work. "
                       "No product pitch, no claims. Goal: start a conversation, "
                       "which is why this is the default for new contacts.",
        "instructions": [
            "Open with a genuine, specific observation about the prospect's work "
            "or role based ONLY on evidence provided.",
            "Do NOT mention the product, features, or pricing in the opener.",
            "End with one lightweight question or an offer to connect, not a pitch.",
            "Keep it short (under 60 words) and human, never templated-looking.",
        ],
    },
    "problem_first": {
        "label": "Problem First",
        "description": "Leads with a concrete problem the prospect is likely to "
                       "have, backed by the evidence, then introduces the "
                       "capability as a possible fit.",
        "instructions": [
            "Lead with ONE specific problem related to the evidence provided. "
            "Do not invent a problem - if the evidence is weak, fall back to a "
            "hypothesis phrased as a question.",
            "Briefly state the capability that addresses that problem.",
            "Avoid jargon and marketing language.",
            "Keep it under 80 words.",
        ],
    },
    "warm_relationship": {
        "label": "Warm Relationship",
        "description": "Leans on an existing relationship: prior conversation, "
                       "shared connection, or past interaction. Personal, "
                       "referential, low sales pressure.",
        "instructions": [
            "Reference the actual relationship: prior conversation topic, "
            "connection context, or shared history - ONLY if evidence exists.",
            "Never fabricate a shared connection or past interaction.",
            "Pivot softly to the capability only if natural.",
            "Keep it under 70 words.",
        ],
    },
}

DEFAULT_STRATEGY = "conversation_first"


def get_strategy(name: str) -> dict:
    name = str(name or DEFAULT_STRATEGY).lower().strip()
    if name not in STRATEGIES:
        name = DEFAULT_STRATEGY
    return STRATEGIES[name]
