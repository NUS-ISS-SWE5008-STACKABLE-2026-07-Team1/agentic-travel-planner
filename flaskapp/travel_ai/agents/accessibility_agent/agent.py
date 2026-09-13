"""Accessibility Agent development and future bias-audit entry point."""

from flaskapp.travel_ai.agents.accessibility_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.agents.accessibility_agent.retrieval import retrieve_accessibility_evidence
from flaskapp.travel_ai.agents.accessibility_agent.guardrails import (
    blocked_input_finding, enforce_accessibility_output, sanitize_evidence,
)
from flaskapp.travel_ai.agents.base import make_specialist_node

NAME = "accessibility_agent"


def _guarded_evidence(state):
    return sanitize_evidence(retrieve_accessibility_evidence(state))


def create_node(llm, tracer, config=None):
    return make_specialist_node(
        NAME, INSTRUCTION, llm, tracer,
        context_provider=_guarded_evidence,
        preflight=blocked_input_finding,
        postprocess=enforce_accessibility_output,
    )
