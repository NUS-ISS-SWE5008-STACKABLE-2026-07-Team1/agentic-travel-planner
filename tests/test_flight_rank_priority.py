"""Ranking order became a parameter; ranking *logic* must not have moved.

`_rank_key` used to be a hand-written 6-tuple. It is now assembled from named
`RANK_COMPONENTS` in a caller-supplied order, so a tool-calling model can ask for
"cheapest" versus "earliest arrival" without being able to invent a component,
drop a tiebreaker, or change what any component measures.

The gate for that refactor is this file plus `test_flight_golden.py`: the default
order must produce byte-identical output to the tuple it replaced, and any
permutation must stay a total order over the same survivor set.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from flaskapp.travel_ai.agents.flight_agent.domain import (
    DEFAULT_RANK_PRIORITY, RANK_COMPONENTS, normalise_priority, propose_flights, rank_leg,
)
from flaskapp.travel_ai.agents.flight_agent.schemas import ArrivalPreference, FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY

SCENARIOS = json.loads(
    (Path(__file__).parent / "golden" / "flight_scenarios.json").read_text()
)


def _requests():
    for scenario in SCENARIOS:
        yield scenario["id"], FlightProposalRequest.model_validate(scenario["request"])


def _legs(proposal_request: FlightProposalRequest):
    """The two calls `propose_flights` makes, as (kwargs, direction) pairs."""
    ctx = proposal_request.trip_context
    return [
        dict(origin=ctx.origin_airports, dest=ctx.dest_airports,
             leg_date=date.fromisoformat(ctx.depart_date), direction="OUTBOUND"),
        dict(origin=ctx.dest_airports, dest=ctx.origin_airports,
             leg_date=date.fromisoformat(ctx.return_date), direction="RETURN"),
    ]


@pytest.mark.parametrize("scenario_id, proposal_request", list(_requests()), ids=[s["id"] for s in SCENARIOS])
def test_default_priority_reproduces_propose_flights(scenario_id, proposal_request):
    """The equivalence gate: `rank_leg` at its default order, called twice, is
    exactly what `propose_flights` returns."""
    expected = propose_flights(proposal_request, SEED_FLIGHT_INVENTORY)
    produced = []
    for leg in _legs(proposal_request):
        produced.extend(rank_leg(SEED_FLIGHT_INVENTORY, proposal_request, top_n=3, **leg))

    assert [c.flight_id for c in produced] == [c.flight_id for c in expected.candidates]
    assert [c.model_dump() for c in produced] == [c.model_dump() for c in expected.candidates]


@pytest.mark.parametrize("scenario_id, proposal_request", list(_requests()), ids=[s["id"] for s in SCENARIOS])
def test_explicit_default_priority_matches_none(scenario_id, proposal_request):
    """Passing the default explicitly and passing nothing must agree — otherwise
    `None` is a third, undocumented ordering."""
    for leg in _legs(proposal_request):
        implicit = rank_leg(SEED_FLIGHT_INVENTORY, proposal_request, top_n=3, **leg)
        explicit = rank_leg(
            SEED_FLIGHT_INVENTORY, proposal_request, top_n=3, priority=DEFAULT_RANK_PRIORITY, **leg
        )
        assert [c.flight_id for c in implicit] == [c.flight_id for c in explicit]


def test_none_priority_is_the_default():
    assert normalise_priority(None) == (DEFAULT_RANK_PRIORITY, [])


def test_omitted_components_are_appended_not_dropped():
    """A partial order must still be a total order, or ranking becomes
    nondeterministic wherever the named components tie."""
    effective, notes = normalise_priority(["prefer_direct"])

    assert effective[0] == "prefer_direct"
    assert set(effective) == set(RANK_COMPONENTS), "a component was dropped"
    assert len(effective) == len(RANK_COMPONENTS), "a component was duplicated"
    assert any("omitted" in note for note in notes)


def test_cost_is_always_terminal():
    """`cost` is the only always-defined continuous component, so it is the only
    reliable tiebreaker. Asking for it first would make every later component
    dead weight."""
    effective, notes = normalise_priority(["cost", "prefer_direct"])

    assert effective[-1] == "cost"
    assert "prefer_direct" in effective
    assert any("cost" in note for note in notes)


def test_cost_last_is_not_reported_as_an_adjustment():
    """Only say something was moved when it actually was."""
    _effective, notes = normalise_priority(["prefer_direct", "cost"])
    assert not any("Moved" in note for note in notes)


def test_unknown_components_are_dropped_with_a_note():
    """Reachable from a model-supplied argument: a bad name must cost ranking
    precision, never crash the request."""
    effective, notes = normalise_priority(["not_a_component", "prefer_direct"])

    assert "not_a_component" not in effective
    assert set(effective) == set(RANK_COMPONENTS)
    assert any("not_a_component" in note for note in notes)


def test_duplicate_components_are_collapsed():
    effective, _notes = normalise_priority(["prefer_direct", "prefer_direct", "avoid_red_eye"])
    assert effective[:2] == ("prefer_direct", "avoid_red_eye")
    assert len(effective) == len(RANK_COMPONENTS)


def test_every_default_component_exists():
    """Guards a typo in either constant — a name in the default order that no
    component implements would raise only at sort time, on some inventories."""
    assert set(DEFAULT_RANK_PRIORITY) == set(RANK_COMPONENTS)


PERMUTATIONS = [
    ["cost"],
    ["arrival_time", "cost"],
    ["prefer_direct", "avoid_red_eye"],
    ["seat_config", "arrival_time", "accessibility_verified"],
    ["not_a_component"],
]


@pytest.mark.parametrize("priority", PERMUTATIONS, ids=[",".join(p) for p in PERMUTATIONS])
def test_permutations_reorder_but_never_change_the_survivor_set(priority):
    """Ranking may reorder; it must never add or remove a candidate. Membership
    is decided by the hard filters, and no permutation of the sort key can reach
    them.

    `top_n` is lifted above the candidate count so truncation cannot mask a
    change in the set itself.
    """
    _id, proposal_request = next(iter(_requests()))
    for leg in _legs(proposal_request):
        baseline = rank_leg(SEED_FLIGHT_INVENTORY, proposal_request, top_n=99, **leg)
        permuted = rank_leg(SEED_FLIGHT_INVENTORY, proposal_request, top_n=99, priority=priority, **leg)

        assert {c.flight_id for c in permuted} == {c.flight_id for c in baseline}


@pytest.mark.parametrize("priority", PERMUTATIONS, ids=[",".join(p) for p in PERMUTATIONS])
def test_ranking_is_deterministic_under_any_priority(priority):
    """Same inputs, same order, twice — the property the terminal `cost`
    tiebreaker exists to guarantee."""
    _id, proposal_request = next(iter(_requests()))
    leg = _legs(proposal_request)[0]
    first = rank_leg(SEED_FLIGHT_INVENTORY, proposal_request, top_n=99, priority=priority, **leg)
    second = rank_leg(SEED_FLIGHT_INVENTORY, proposal_request, top_n=99, priority=priority, **leg)

    assert [c.flight_id for c in first] == [c.flight_id for c in second]


def _with_soft_arrival(proposal_request: FlightProposalRequest) -> FlightProposalRequest:
    """The same request, plus a SOFT outbound arrival preference.

    `_component_arrival_time` is deliberately inert without one — see
    `test_arrival_time_is_inert_without_a_soft_preference`.
    """
    ctx = proposal_request.trip_context
    prefs = ctx.flight_preferences.model_copy(update={
        "arrival_preferences": [
            ArrivalPreference(direction="OUTBOUND", by="18:00", hard=False)
        ]
    })
    return proposal_request.model_copy(
        update={"trip_context": ctx.model_copy(update={"flight_preferences": prefs})}
    )


def test_priority_actually_changes_the_order():
    """If no permutation ever reordered anything, every other test here would
    pass vacuously."""
    proposal_request = _with_soft_arrival(
        FlightProposalRequest.model_validate(
            next(s for s in SCENARIOS if s["id"] == "golden_scenario_1_round0")["request"]
        )
    )
    leg = _legs(proposal_request)[0]
    by_arrival = rank_leg(
        SEED_FLIGHT_INVENTORY, proposal_request, top_n=99, priority=["arrival_time"], **leg
    )

    assert len(by_arrival) > 1, "scenario is too small to demonstrate reordering"
    arrivals = [c.arr_ts for c in by_arrival]
    assert arrivals == sorted(arrivals), "arrival_time priority did not sort by arrival"


def test_arrival_time_is_inert_without_a_soft_preference():
    """A real constraint on the `rank_flights` tool, not an accident.

    `_component_arrival_time` returns a constant unless the traveller stated a
    SOFT arrival preference, so asking to rank by arrival time has no effect for
    a traveller who never expressed one — cost then breaks every tie. This is
    what keeps the default key byte-identical for the majority of requests, so it
    must not be "fixed"; the tool layer should disclose it instead.
    """
    proposal_request = FlightProposalRequest.model_validate(
        next(s for s in SCENARIOS if s["id"] == "golden_scenario_1_round0")["request"]
    )
    assert not proposal_request.trip_context.flight_preferences.arrival_preferences
    leg = _legs(proposal_request)[0]

    by_cost = rank_leg(SEED_FLIGHT_INVENTORY, proposal_request, top_n=99, priority=["cost"], **leg)
    by_arrival = rank_leg(
        SEED_FLIGHT_INVENTORY, proposal_request, top_n=99, priority=["arrival_time"], **leg
    )

    assert [c.flight_id for c in by_arrival] == [c.flight_id for c in by_cost]
