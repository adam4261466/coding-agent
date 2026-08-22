"""Fully automatic LinkedIn outreach agent.

Runs the complete pipeline in a closed loop:
  discover -> research -> qualify -> segment -> produce -> execute -> monitor -> decide -> repeat

Key separation:
  - LLM decides what action to take next (planner)
  - Browser observes reality (observer)
  - Code performs LinkedIn actions (executor)
  - SQLite records state (event_log)
"""
