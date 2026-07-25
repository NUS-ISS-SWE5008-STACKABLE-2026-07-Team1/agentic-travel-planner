"""Accessibility Agent development and future bias-audit entry point."""

from flaskapp.travel_ai.agents.accessibility_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.agents.base import make_specialist_node

NAME = "accessibility_agent"


def create_node(llm, tracer):
    return make_specialist_node(NAME, INSTRUCTION, llm, tracer)
