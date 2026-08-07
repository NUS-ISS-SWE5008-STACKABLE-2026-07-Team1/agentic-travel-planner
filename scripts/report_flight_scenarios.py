"""Print each flight scenario's INPUT and RESULT, no model required.

`pytest -v` reports which tests passed but not what they fed in or what came
back. This walks the same scenarios and prints both, so the deterministic
behaviour can be read and checked by hand rather than taken on trust.

Every scenario here is deterministic — no LLM call, no network — so the output
is reproducible and safe to paste into a report.

Usage:
    python scripts/report_flight_scenarios.py > docs/test_reports/flight-scenarios.txt
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flaskapp.travel_ai.agents.flight_agent.adapter import to_flight_request
from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights, screen_flights
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY
from flaskapp.travel_ai.schemas import TravelRequest

GOLDEN_PATH = Path(__file__).parent.parent / "tests" / "golden" / "flight_scenarios.json"

FORM_BASE = dict(
    origin="Singapore", destination="Japan",
    departure_date=date(2026, 9, 1), return_date=date(2026, 9, 5),
    travellers=1, traveller_ages=[34], traveller_genders=["female"],
    traveller_accessibility_needs=[[]], budget=4000, currency="SGD",
    preferences=[], accessibility_needs=[],
)


def header(text: str, char: str = "=") -> None:
    print(f"\n{char * 78}\n{text}\n{char * 78}")


def show_proposal(proposal) -> None:
    if not proposal.candidates:
        print("  RESULT: no candidates (no feasible option)")
        return
    for direction in ("OUTBOUND", "RETURN"):
        legs = [c for c in proposal.candidates if c.direction == direction]
        print(f"  {direction}:")
        if not legs:
            print("    (none — no flight satisfies the constraints for this leg)")
        for rank, c in enumerate(legs, 1):
            fee = f" + {c.seat_fee_estimate:.0f} seat fees" if c.seat_fee_estimate else ""
            print(f"    {rank}. {c.flight_id:20} dep {c.dep_ts}  arr {c.arr_ts}  "
                  f"SGD {c.price:.0f}{fee}  stops {c.stops}  wheelchair={c.wheelchair_assist_available}")


def golden_scenarios() -> None:
    header("PART 1 — GOLDEN SCENARIOS (tests/golden/flight_scenarios.json)")
    print("These are the fixtures test_flight_golden.py asserts against. The tool's")
    print("output must match 'expected' exactly for the test to pass.\n")

    for scenario in json.loads(GOLDEN_PATH.read_text(encoding="utf-8")):
        header(f"{scenario['id']}", "-")
        print(f"WHY: {scenario['description']}\n")

        request = FlightProposalRequest.model_validate(scenario["request"])
        ctx = request.trip_context
        print("INPUT:")
        print(f"  route          {ctx.origin_airport} -> {ctx.dest_airport}")
        print(f"  dates          out {ctx.depart_date}, back {ctx.return_date}")
        print(f"  party          {ctx.party}   budget {ctx.budget_total}")
        print(f"  accessibility  {ctx.accessibility_needs}")
        if request.constraints:
            supplied = request.constraints.model_dump(exclude_none=True, exclude_defaults=True)
            print(f"  constraints    {supplied}   (orchestrator-issued, renegotiation only)")
        else:
            print("  constraints    none (round 0)")

        proposal = propose_flights(request, SEED_FLIGHT_INVENTORY)
        print("\nRESULT:")
        show_proposal(proposal)

        outbound = [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]
        inbound = [c.flight_id for c in proposal.candidates if c.direction == "RETURN"]
        expected = scenario["expected"]
        match = outbound == expected["outbound_ids"] and inbound == expected["inbound_ids"]
        print(f"\n  expected outbound {expected['outbound_ids']}")
        print(f"  expected inbound  {expected['inbound_ids']}")
        print(f"  MATCHES EXPECTED: {match}")

        rejected = [s for s in screen_flights(request, SEED_FLIGHT_INVENTORY) if not s.included]
        if rejected:
            print(f"\n  WHY OTHERS WERE REJECTED ({len(rejected)} rows; first 5 shown):")
            for entry in rejected[:5]:
                print(f"    {entry.flight_id:20} {entry.reasons[0]}")


def form_scenarios() -> None:
    header("PART 2 — WHAT THE INTAKE FORM ACTUALLY SENDS")
    print("Same deterministic tool, driven by real form payloads rather than fixtures.")
    print("This is the path the running app takes.\n")

    cases = [
        ("Covered route, no special needs", {}),
        ("Wheelchair need typed as free text",
         {"accessibility_needs": ["Traveler 1: wheelchair assistance"],
          "traveller_accessibility_needs": [["wheelchair assistance"]]}),
        ("Family of 4 (2 adults, 2 children)",
         {"travellers": 4, "traveller_ages": [40, 38, 9, 6],
          "traveller_genders": ["female", "male", "male", "female"],
          "traveller_accessibility_needs": [[], [], [], []]}),
        ("Preference 'direct flights' in free text", {"preferences": ["direct flights"]}),
        ("Route with no inventory (Brazil)", {"destination": "Brazil"}),
        ("Country with no airport mapping (Chad)", {"destination": "Chad"}),
        ("Dates outside the loaded inventory",
         {"departure_date": date(2026, 12, 10), "return_date": date(2026, 12, 20)}),
    ]

    for label, overrides in cases:
        header(label, "-")
        payload = {**FORM_BASE, **overrides}
        travel_request = TravelRequest(**payload)
        print("INPUT (as posted by the form):")
        print(f"  {travel_request.origin} -> {travel_request.destination}, "
              f"{travel_request.departure_date} to {travel_request.return_date}")
        print(f"  travellers {travel_request.travellers}, ages {travel_request.traveller_ages}, "
              f"budget {travel_request.budget} {travel_request.currency}")
        print(f"  preferences {travel_request.preferences}")
        print(f"  accessibility_needs {travel_request.accessibility_needs}")

        adapted = to_flight_request(travel_request)
        ctx = adapted.request.trip_context
        print("\nADAPTED TO FLIGHT CONTRACT:")
        print(f"  airports       {ctx.origin_airport} -> {ctx.dest_airport}")
        print(f"  party derived  {ctx.party}")
        print(f"  prefer_direct={ctx.flight_preferences.prefer_direct}  "
              f"avoid_red_eye={ctx.flight_preferences.avoid_red_eye}")
        print(f"  needs cleaned  {ctx.accessibility_needs}")
        print(f"  routable={adapted.is_routable}  has_inventory={adapted.has_inventory}")
        if adapted.unresolved:
            for note in adapted.unresolved:
                print(f"  UNRESOLVED: {note}")

        print("\nRESULT:")
        show_proposal(propose_flights(adapted.request, SEED_FLIGHT_INVENTORY))
        if not adapted.has_inventory:
            print("  -> the graph node falls back to the prompt-only path here,")
            print("     labelling its options as estimates rather than verified inventory.")


def bias_control() -> None:
    header("PART 3 — BIAS AUDIT CONTROL (XRAI)")
    print("Ranking must be identical across genders, so any difference an audit")
    print("observes in the model's rationale is attributable to the LLM alone.\n")

    baseline = None
    for gender in ("female", "male", "non_binary", "prefer_not_to_say"):
        payload = {**FORM_BASE, "traveller_genders": [gender], "preferences": ["direct flights"]}
        proposal = propose_flights(
            to_flight_request(TravelRequest(**payload)).request, SEED_FLIGHT_INVENTORY
        )
        ids = [c.flight_id for c in proposal.candidates]
        if baseline is None:
            baseline = ids
        print(f"  traveller_genders=['{gender}']".ljust(48)
              + f"-> {ids}  identical={ids == baseline}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("FLIGHT AGENT — SCENARIO INPUT/RESULT LOG")
    print("Deterministic only: no model call, no network. Reproducible on any machine.")
    golden_scenarios()
    form_scenarios()
    bias_control()
    header("END")


if __name__ == "__main__":
    main()
