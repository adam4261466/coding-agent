"""Bounded browser research agent for one prospect.

Wraps the project's existing browser Agent (agent.py) with a research-only
system prompt and a hard budget, so the model never browses LinkedIn freely.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(ROOT)
# PROJECT_ROOT must be searched BEFORE ROOT: otherwise the package
# linkedin_intelligence/agent/ shadows the top-level agent.py module
# (the base browser Agent) and "from agent import Agent" fails.
sys.path[:] = [p for p in sys.path if p not in (ROOT, PROJECT_ROOT)]
sys.path.insert(0, ROOT)
sys.path.insert(0, PROJECT_ROOT)

try:
    from agent import Agent
except ImportError:
    Agent = None

from ..utils import load_icp

RESEARCH_SYSTEM_PROMPT = """You are a LINKEDIN RESEARCH AGENT. You receive ONE
research task and must complete it within a strict budget.

RULES:
- Open ONLY the profile URL given in the task. Never wander LinkedIn.
- After navigating, always browser_snapshot() before acting on any index.
- IMPORTANT: After the initial snapshot, SCROLL DOWN to see the full profile:
  * Scroll down at least 2-3 times to reach Experience, About, and Education sections.
  * After each scroll, call browser_snapshot() to capture the new content.
  * The most valuable information (job history, skills, about section) is BELOW the fold.
- Extract ALL information relevant to the task's required fields:
  * Current and past job titles, companies, and durations
  * Education (schools, degrees, fields of study)
  * About/summary section content
  * Skills and endorsements
  * Any pain signals: complaints, "open to work", job changes, new roles
- Do not guess or fabricate facts. Every finding needs visible evidence.
- Do not send messages, connect requests, or click on any messaging/compose
  elements. Research only.
- When you have enough evidence (or hit the budget), STOP and return a single
  JSON object:
  {{"findings": [{{"claim": "...", "evidence": "...", "source": "profile",
  "confidence": 0.9}}], "summary": "..."}}

Current directory: {cwd}
"""


def _extract_findings(text: str) -> list:
    if not text:
        return []
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    findings = data.get("findings", []) if isinstance(data, dict) else []
    return [f for f in findings if isinstance(f, dict)]


def run_research(task: dict, prospect: dict, model: str = "gemma4:31b-cloud",
                 base_url: str = "http://localhost:11434",
                 store=None, offline: bool = False) -> dict:
    """Execute a single research task. `offline=True` skips the browser."""
    from .campaign import task_prompt
    from .evidence import record_findings

    if Agent is None:
        raise ImportError("Could not import the base browser Agent (agent.py).")

    if offline or not prospect.get("linkedin_url"):
        return {"task_id": task["task_id"], "offline": True, "findings": [],
                "summary": "Offline mode: no browser research performed."}

    agent = Agent(model=model, base_url=base_url)
    cwd = os.getcwd()
    agent.messages = [{"role": "system",
                       "content": RESEARCH_SYSTEM_PROMPT.format(cwd=cwd)}]

    budget = task.get("budget", {})
    # Bound the loop; the base agent allows 25 steps by default.
    import agent as base_agent
    original_max = base_agent.MAX_AGENT_STEPS
    base_agent.MAX_AGENT_STEPS = max(1, int(budget.get("max_steps", 8)))
    try:
        result = agent.run(task_prompt(task, prospect))
    except Exception as exc:
        # Playwright EPIPE / broken pipe / browser context errors are common
        # and should not kill the entire Phase 2 run. Treat as offline.
        print(f"         [agent] browser error: {exc}")
        return {
            "task_id": task["task_id"],
            "offline": True,
            "findings": [],
            "summary": f"Browser error: {exc}",
        }
    finally:
        base_agent.MAX_AGENT_STEPS = original_max

    findings = _extract_findings(result)
    if store:
        record_findings(store, prospect["prospect_id"], findings)
    return {
        "task_id": task["task_id"],
        "offline": False,
        "findings": findings,
        "summary": result[:2000],
    }
