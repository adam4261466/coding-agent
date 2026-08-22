"""4-layer agent memory system.

  Events   — what happened (immutable)
  State    — what is true now (derived, updatable)
  Facts    — what we know (evidence-backed)
  Tasks    — what we are trying to accomplish (objectives)

The memory_service.py module exposes one unified API:
  memory.get_prospect_context(prospect_id)

That returns everything the planner needs in a single call.
"""
