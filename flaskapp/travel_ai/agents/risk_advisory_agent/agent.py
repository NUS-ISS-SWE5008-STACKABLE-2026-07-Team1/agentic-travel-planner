"""Risk & Advisory Agent development and tool-integration entry point."""

from flaskapp.travel_ai.agents.base import make_specialist_node
from flaskapp.travel_ai.agents.risk_advisory_agent.prompt import INSTRUCTION

NAME = "risk_advisory_agent"


def create_node(llm, tracer):
    return make_specialist_node(NAME, INSTRUCTION, llm, tracer)
