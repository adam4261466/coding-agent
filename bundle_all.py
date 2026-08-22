import os

files = [
    "run_agent.py",
    "agent.py",
    "linkedin_intelligence/agent/orchestrator.py",
    "linkedin_intelligence/agent/planner.py",
    "linkedin_intelligence/agent/executor.py",
    "linkedin_intelligence/automation/actions/browser_executor.py",
    "linkedin_intelligence/automation/actions/linkedin_actions.py",
    "linkedin_intelligence/automation/memory/memory_service.py",
    "linkedin_intelligence/automation/memory/event_store.py",
    "linkedin_intelligence/automation/memory/state_store.py",
    "linkedin_intelligence/automation/memory/fact_store.py",
    "linkedin_intelligence/automation/memory/task_store.py",
    "linkedin_intelligence/automation/memory/snapshot_store.py",
    "linkedin_intelligence/automation/monitoring/eligibility.py",
    "linkedin_intelligence/automation/monitoring/handoff.py",
    "linkedin_intelligence/outreach/pipeline.py",
    "linkedin_intelligence/outreach/state_machine.py",
    "linkedin_intelligence/outreach/campaign.py",
    "linkedin_intelligence/outreach/approval.py",
    "linkedin_intelligence/outreach/sequence.py",
    "linkedin_intelligence/outreach/conversation.py",
    "linkedin_intelligence/outreach/message_generator.py",
    "linkedin_intelligence/outreach/message_strategy.py",
    "linkedin_intelligence/outreach/validator.py",
    "linkedin_intelligence/outreach/eligibility.py",
    "linkedin_intelligence/research/agent.py",
    "linkedin_intelligence/research/campaign.py",
    "linkedin_intelligence/research/evidence.py",
    "linkedin_intelligence/research/next_action.py",
    "linkedin_intelligence/research/qualifier.py",
    "linkedin_intelligence/store.py",
    "linkedin_intelligence/utils.py",
    "linkedin_intelligence/config/campaigns/ai_engineers.yaml",
    "linkedin_intelligence/config/campaigns/data_engineers.yaml",
    "linkedin_intelligence/config/campaigns/founders.yaml",
]

output = "ALL_SOURCE_FILES.txt"
with open(output, "w", encoding="utf-8") as out:
    for f in files:
        if not os.path.exists(f):
            out.write(f"\n{'='*80}\n")
            out.write(f"# FILE: {f} [MISSING]\n")
            out.write(f"{'='*80}\n\n")
            continue
        with open(f, "r", encoding="utf-8") as fh:
            content = fh.read()
        out.write(f"\n{'='*80}\n")
        out.write(f"# FILE: {f}\n")
        out.write(f"{'='*80}\n\n")
        out.write(content)
        out.write("\n")

print(f"Written to {output} ({os.path.getsize(output)} bytes)")
