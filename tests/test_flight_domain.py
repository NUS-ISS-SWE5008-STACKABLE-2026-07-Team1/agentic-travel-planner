from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights, screen_flights
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightConstraints,
    FlightInventoryItem,
    FlightProposalRequest,
    TripContext,
)
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY

BASE_CONTEXT = dict(
    origin_airport="SIN",
    dest_airport="NRT",
    dest_city="Tokyo",
    dest_country="JP",
    depart_date="2026-09-01",
    return_date="2026-09-05",
    party={"adults": 1, "children": 0},
    budget_total=4000,
    accessibility_needs=["wheelchair"],
    passport_country="SG",
)


def _request(**overrides) -> FlightProposalRequest:
    context_kwargs = {**BASE_CONTEXT, **overrides.pop("context", {})}
    return FlightProposalRequest(trip_context=TripContext(**context_kwargs), **overrides)


def test_ranks_cheapest_first_per_leg():
    proposal = propose_flights(_request(), SEED_FLIGHT_INVENTORY)
    outbound = [c for c in proposal.candidates if c.direction == "OUTBOUND"]
    inbound = [c for c in proposal.candidates if c.direction == "RETURN"]
    assert [c.flight_id for c in outbound] == ["SQ636-20260901", "SQ632-20260901"]
    assert [c.flight_id for c in inbound] == ["SQ637-20260905", "SQ633-20260905"]


def test_arrive_before_constraint_filters_the_red_eye():
    """Golden scenario #1: 23:40 red-eye becomes infeasible on renegotiation,
    so the re-proposal request carries arrive_before and must drop SQ636."""
    request = _request(constraints=FlightConstraints(arrive_before="2026-09-01T23:00+09:00"))
    proposal = propose_flights(request, SEED_FLIGHT_INVENTORY)
    outbound_ids = [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]
    assert outbound_ids == ["SQ632-20260901"]


def test_max_price_constraint_filters_expensive_options():
    request = _request(constraints=FlightConstraints(max_price=400))
    proposal = propose_flights(request, SEED_FLIGHT_INVENTORY)
    assert [c.flight_id for c in proposal.candidates] == [
        "SQ636-20260901",
        "SQ637-20260905",
    ]


def test_implicit_wheelchair_filter_from_accessibility_needs():
    inaccessible = FlightInventoryItem(
        flight_id="XX100-20260901",
        carrier="XX",
        flight_no="XX100",
        origin_airport="SIN",
        dest_airport="NRT",
        dep_ts="2026-09-01T06:00+08:00",
        arr_ts="2026-09-01T13:00+09:00",
        duration_min=360,
        price=200,
        cabin_class="ECONOMY",
        seats_available=5,
        stops=0,
        wheelchair_assist_available=False,
        step_free_boarding=False,
    )
    request = _request()  # no explicit constraints -> falls back to trip_context needs
    proposal = propose_flights(request, [inaccessible, *SEED_FLIGHT_INVENTORY])
    outbound_ids = [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]
    assert "XX100-20260901" not in outbound_ids


def test_explicit_constraints_supersede_implicit_needs_check():
    """On renegotiation the orchestrator passes explicit constraints; those
    are authoritative and no longer fall back to trip_context.accessibility_needs."""
    accessible_but_pricier = SEED_FLIGHT_INVENTORY[1]  # SQ632, wheelchair True
    request = _request(
        constraints=FlightConstraints(require_wheelchair_assist=False, max_price=9999),
        context={"accessibility_needs": ["wheelchair"]},
    )
    proposal = propose_flights(request, SEED_FLIGHT_INVENTORY)
    assert any(c.flight_id == accessible_but_pricier.flight_id for c in proposal.candidates)


def test_zero_seats_excludes_flight():
    sold_out = SEED_FLIGHT_INVENTORY[0].model_copy(update={"seats_available": 0})
    request = _request()
    proposal = propose_flights(request, [sold_out])
    assert not any(c.direction == "OUTBOUND" for c in proposal.candidates)


def test_top_n_limits_candidates_per_leg():
    request = _request()
    proposal = propose_flights(request, SEED_FLIGHT_INVENTORY, top_n=1)
    outbound = [c for c in proposal.candidates if c.direction == "OUTBOUND"]
    inbound = [c for c in proposal.candidates if c.direction == "RETURN"]
    assert len(outbound) == 1
    assert len(inbound) == 1


def test_no_candidates_for_unmatched_date_is_empty_not_error():
    """A date the dataset does not cover yields no candidates, not an exception.

    The date has to sit past the end of the seed calendar, not merely in a gap
    between stocked dates. It used to be 2026-12-25, which was outside the data
    entirely until the calendar was extended through 31 December 2026 — after
    that it passed only because the SIN-NRT cadence happened to skip Christmas
    Day, so a change to the step cycle would have quietly stopped this test
    proving anything.
    """
    request = _request(context={"depart_date": "2027-06-15", "return_date": "2027-06-22"})
    proposal = propose_flights(request, SEED_FLIGHT_INVENTORY)
    assert not any(c.direction == "OUTBOUND" for c in proposal.candidates)


def test_screen_flights_records_every_same_route_row_with_reasons():
    """Post-tool traceability gate — rejections need a reason on record too,
    not just survivors (XRAI notebook 7 pattern, discussion_18Jul.md)."""
    request = _request(constraints=FlightConstraints(direction="OUTBOUND", max_price=400))
    results = screen_flights(request, SEED_FLIGHT_INVENTORY)

    excluded_outbound = next(
        r for r in results if r.direction == "OUTBOUND" and r.flight_id == "SQ632-20260901"
    )
    assert excluded_outbound.included is False
    assert "max_price" in excluded_outbound.reasons[0]

    included_outbound = next(
        r for r in results if r.direction == "OUTBOUND" and r.flight_id == "SQ636-20260901"
    )
    assert included_outbound.included is True
    assert included_outbound.reasons == []
