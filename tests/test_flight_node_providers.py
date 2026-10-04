"""The node against a non-seed provider.

`test_flight_node.py` covers the seed path. This covers what changes when
inventory comes from somewhere that can fail, can return nothing, and cannot
answer accessibility questions — the three things a static dataset never does.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.agents.flight_agent.agent import (
    ESTIMATE_WARNING,
    NAME,
    UNVERIFIED_WHEELCHAIR,
    create_node,
)
from flaskapp.travel_ai.agents.flight_agent.providers.base import InventoryResult
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightAgentResponse,
    FlightInventoryItem,
)
from flaskapp.travel_ai.schemas import AgentFinding, TravelRequest
from flaskapp.travel_ai.tracing import AuditTracer

TRIP = dict(
    origin="United Kingdom", destination="United States",
    departure_date=date(2026, 9, 1), return_date=date(2026, 9, 5),
    travellers=1, traveller_ages=[34], traveller_genders=["female"],
    traveller_accessibility_needs=[["wheelchair assistance"]],
    budget=4000, currency="GBP",
    preferences=[],
    accessibility_needs=["Traveler 1: wheelchair assistance"],
)
# What a live provider would say about its own rows; deliberately not the seed
# wording, so a test can tell which one reached the options.
LIVE_ASSUMPTION = "Fare and schedule come from a live supplier search and expire quickly."

# Singapore->Japan is stocked by the seed dataset, so this reaches the grounded
# path under the seed provider — which is what the fallback-to-seed test needs.
SEED_TRIP = {**TRIP, "origin": "Singapore", "destination": "Japan", "currency": "SGD"}


def _live_flight(flight_id: str, origin: str, dest: str, dep: str, arr: str) -> FlightInventoryItem:
    return FlightInventoryItem(
        flight_id=flight_id, carrier="ZZ", flight_no=flight_id,
        origin_airport=origin, dest_airport=dest, dep_ts=dep, arr_ts=arr,
        duration_min=430, price=300.0, cabin_class="ECONOMY",
        seats_available=1, stops=0,
        wheelchair_assist_available=None, step_free_boarding=None,
    )


LIVE_INVENTORY = [
    _live_flight("off_a:0", "LHR", "JFK", "2026-09-01T08:00:00+01:00", "2026-09-01T11:10:00-04:00"),
    _live_flight("off_a:1", "JFK", "LHR", "2026-09-05T18:00:00-04:00", "2026-09-06T06:00:00+01:00"),
]


class FakeProvider:
    """A stand-in live provider whose result the test dictates."""

    name = "fake_live"
    assumption = LIVE_ASSUMPTION

    def __init__(self, result: InventoryResult, covers: bool = True):
        self._result = result
        self._covers = covers
        self.fetches = 0

    def covers(self, request) -> bool:
        return self._covers

    def fetch(self, request) -> InventoryResult:
        self.fetches += 1
        return self._result


class StubStructured:
    def __init__(self, response):
        self._response = response

    def invoke(self, messages, config=None):
        return self._response


class StubLLM:
    def __init__(self, flight_response=None, finding=None):
        self.flight_response = flight_response
        self.finding = finding

    def with_structured_output(self, schema, method=None):
        return StubStructured(
            self.flight_response if schema is FlightAgentResponse else self.finding
        )


def _state(payload: dict) -> dict:
    request = TravelRequest(**payload)
    correlation_id = uuid4()
    message = request_message(
        correlation_id=correlation_id, sender="orchestrator_agent", recipient=NAME,
        payload_type="TravelRequest", payload=request,
    )
    return {
        "request_id": str(correlation_id),
        "request": request.model_dump(mode="json"),
        "findings": [],
        "messages": [message],
    }


@pytest.fixture
def tracer(tmp_path):
    return AuditTracer(tmp_path, "req-1")


def _llm():
    """Serves both paths: a flight response for the grounded node, and an
    AgentFinding for the prompt-only fallback the node shares with every other
    specialist."""
    return StubLLM(
        flight_response=FlightAgentResponse(
            rationale="Both legs are direct.", highlighted_flight_ids=[], confidence=0.8
        ),
        finding=AgentFinding(
            agent=NAME, summary="Estimated routings.", options=[], warnings=[], confidence=0.4
        ),
    )


def test_live_inventory_reaches_the_finding(tracer):
    provider = FakeProvider(InventoryResult(items=LIVE_INVENTORY, notes=["seat counts unavailable"]))
    finding = create_node(_llm(), tracer, provider=provider)(_state(TRIP))["findings"][0]

    assert {o.name.split(" ")[0] for o in finding.options} == {"off_a:0", "off_a:1"}
    assert "seat counts unavailable" in finding.warnings


def test_the_provider_is_fetched_once_per_request(tracer):
    """The proposal and the screening trace must see the identical row set, and
    a live provider bills and blocks on every call."""
    provider = FakeProvider(InventoryResult(items=LIVE_INVENTORY))
    create_node(_llm(), tracer, provider=provider)(_state(TRIP))
    assert provider.fetches == 1


def test_options_carry_the_providers_own_assumption_text(tracer):
    """The static-dataset wording would be a false statement about a live fare."""
    provider = FakeProvider(InventoryResult(items=LIVE_INVENTORY))
    finding = create_node(_llm(), tracer, provider=provider)(_state(TRIP))["findings"][0]
    assert all(LIVE_ASSUMPTION in o.assumptions for o in finding.options)


def test_unverified_accessibility_is_disclosed_on_every_option(tracer):
    provider = FakeProvider(InventoryResult(items=LIVE_INVENTORY))
    finding = create_node(_llm(), tracer, provider=provider)(_state(TRIP))["findings"][0]
    assert all(UNVERIFIED_WHEELCHAIR in o.limitations for o in finding.options)


def test_a_failed_live_search_degrades_to_the_estimate_path(tracer):
    """A supplier outage must not fail the graph node — it becomes a labelled
    estimate, exactly like an unstocked route."""
    provider = FakeProvider(InventoryResult(notes=["Live flight search could not be reached (Timeout)."]))
    finding = create_node(_llm(), tracer, provider=provider)(_state(TRIP))["findings"][0]

    assert ESTIMATE_WARNING in finding.warnings
    assert "could not be reached" in " ".join(finding.warnings)


def test_a_route_the_provider_does_not_cover_is_never_fetched(tracer):
    provider = FakeProvider(InventoryResult(items=LIVE_INVENTORY), covers=False)
    create_node(_llm(), tracer, provider=provider)(_state(TRIP))
    assert provider.fetches == 0


def test_an_unknown_source_warns_the_traveller_it_fell_back_to_seed(tracer):
    """A misconfigured source silently serving static data would hide the
    change — the fallback has to be visible even on the grounded path, where
    nothing else would hint the source had changed."""
    config = {"FLIGHT_INVENTORY_SOURCE": "live"}
    finding = create_node(_llm(), tracer, config=config)(_state(SEED_TRIP))["findings"][0]
    assert finding.options, "the seed fallback should still produce grounded options"
    assert any("Unknown FLIGHT_INVENTORY_SOURCE" in w for w in finding.warnings)
