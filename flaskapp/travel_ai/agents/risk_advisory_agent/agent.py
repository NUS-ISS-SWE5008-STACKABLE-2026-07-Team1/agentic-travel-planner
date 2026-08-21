"""Risk & Advisory Agent development and tool-integration entry point.

Prompt-only: unlike Flight and Hotel there is no deterministic inventory to
ground against, so screening is the only control this agent has. It previously
had none — `make_specialist_node`'s `preflight`/`postprocess` hooks existed but
were left unused here, which made this the one specialist that would pass
traveller free text straight to the model and return generated prose unchecked.
"""

from flaskapp.travel_ai.agents.base import make_specialist_node
from flaskapp.travel_ai.agents.risk_advisory_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.guardrails.specialist import make_postprocess, make_preflight

NAME = "risk_advisory_agent"


def create_node(llm, tracer):
    return make_specialist_node(
        NAME, INSTRUCTION, llm, tracer,
        preflight=make_preflight(NAME),
        postprocess=make_postprocess(NAME),
    )
