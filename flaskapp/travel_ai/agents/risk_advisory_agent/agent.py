"""Risk & Advisory Agent development and tool-integration entry point.

Prompt-only: unlike Flight and Hotel there is no deterministic inventory to
ground against, so screening plus reference-data grounding (`guardrails.py`)
are the only controls this agent has.
"""

from flaskapp.travel_ai.agents.base import make_specialist_node
from flaskapp.travel_ai.agents.risk_advisory_agent.guardrails import (
    postprocess,
    preflight,
    trip_context,
)
from flaskapp.travel_ai.agents.risk_advisory_agent.prompt import INSTRUCTION

NAME = "risk_advisory_agent"


def create_node(llm, tracer):
    return make_specialist_node(
        NAME, INSTRUCTION, llm, tracer,
        context_provider=trip_context,
        preflight=preflight,
        postprocess=postprocess,
    )
