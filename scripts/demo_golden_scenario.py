"""Live demo: the flagship golden scenario (db_schema.md §7 - wheelchair user,
SIN->Tokyo, budget SGD 4,000) through a REAL model, round 0 then the renegotiation.

Reuses tests/golden/flight_scenarios.json directly (single source of truth - the
same fixture the mocked golden-suite tests already assert against structurally;
this script is the first thing to actually run it through a real LLM rather than
a scripted response). Unlike demo_multi_gap_relaxation.py, this is not testing for
a specific comparison - it's "what does the real rationale actually read like,"
which is the thing the prompt lifecycle (llmops_plan.md §8) says can't be judged
until a real model has been observed.

Needs a provider configured in .env/.env.secrets. Usage:
    python scripts/demo_golden_scenario.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts._llm import build_demo_llm, use_utf8_stdout
from flaskapp.travel_ai.agents.flight_agent.reasoning import run_flight_agent
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY

GOLDEN_PATH = Path(__file__).parent.parent / "tests" / "golden" / "flight_scenarios.json"
SCENARIO_IDS = ["golden_scenario_1_round0", "golden_scenario_1_renegotiation_arrive_before"]


def _load_scenario(scenario_id: str) -> dict:
    scenarios = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return next(s for s in scenarios if s["id"] == scenario_id)


def _run(scenario: dict, llm):
    request = FlightProposalRequest.model_validate(scenario["request"])
    proposal, response = run_flight_agent(request, SEED_FLIGHT_INVENTORY, llm)

    print(f"\n{'=' * 70}\n{scenario['id']}\n{'=' * 70}")
    print(f"description: {scenario['description']}")
    outbound = [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]
    inbound = [c.flight_id for c in proposal.candidates if c.direction == "RETURN"]
    print(f"outbound (tool, deterministic): {outbound}")
    print(f"inbound  (tool, deterministic): {inbound}")
    print(f"expected outbound: {scenario['expected']['outbound_ids']}")
    print(f"expected inbound:  {scenario['expected']['inbound_ids']}")
    print(f"tool output matches golden expectation: "
          f"{outbound == scenario['expected']['outbound_ids'] and inbound == scenario['expected']['inbound_ids']}")
    print(f"\n--- LLM rationale (this is the part never seen live before) ---")
    print(response.rationale)
    print(f"\nhighlighted_flight_ids: {response.highlighted_flight_ids}")
    print(f"confidence: {response.confidence}")
    print(f"escalate: {response.escalate}"
          + (f" ({response.escalation_reason})" if response.escalation_reason else ""))
    return proposal, response


def main() -> None:
    use_utf8_stdout()
    llm, reason = build_demo_llm()
    if llm is None:
        print(f"No usable model configured ({reason}) - this script needs a real "
              "model to run. It's ready; configure a provider in .env/.env.secrets "
              "and re-run.")
        return

    for scenario_id in SCENARIO_IDS:
        _run(_load_scenario(scenario_id), llm)


if __name__ == "__main__":
    main()
