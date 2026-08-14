"""Golden-dataset runner for Flight Agent (llmops_plan.md §2b/§9 testing philosophy).

Scenarios are versioned JSON (golden/flight_scenarios.json), not hardcoded in Python,
so new failures found later become permanent, reviewable test cases (llmops_plan.md
§8 stage 4). Domain logic is fully deterministic (no LLM), so exact-match assertions
are appropriate here per db_schema.md's guidance to reserve exact-match for
deterministic scenarios.
"""

import json
from pathlib import Path

import pytest

from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY

SCENARIOS = json.loads((Path(__file__).parent / "golden" / "flight_scenarios.json").read_text())
KNOWN_FLIGHT_IDS = {item.flight_id for item in SEED_FLIGHT_INVENTORY}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_golden_scenario(scenario):
    request = FlightProposalRequest.model_validate(scenario["request"])
    proposal = propose_flights(request, SEED_FLIGHT_INVENTORY)

    # Grounding invariant (llmops_plan.md §3): never propose a flight that
    # doesn't exist in the agent's own inventory, on every scenario.
    for candidate in proposal.candidates:
        assert candidate.flight_id in KNOWN_FLIGHT_IDS, (
            f"{scenario['id']}: fabricated flight_id {candidate.flight_id!r}"
        )

    outbound_ids = [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]
    inbound_ids = [c.flight_id for c in proposal.candidates if c.direction == "RETURN"]
    assert outbound_ids == scenario["expected"]["outbound_ids"], scenario["description"]
    assert inbound_ids == scenario["expected"]["inbound_ids"], scenario["description"]
