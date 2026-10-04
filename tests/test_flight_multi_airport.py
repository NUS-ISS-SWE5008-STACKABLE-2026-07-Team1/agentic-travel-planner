"""Multi-airport cities: the behaviour city intake exists to enable.

Country granularity forced one gateway per country, so a Tokyo trip silently
meant Narita and a Haneda fare could never be shown, however cheap. These tests
pin the fix in route matching in `domain.py`.
"""

from __future__ import annotations

from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights, screen_flights
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightInventoryItem,
    FlightProposalRequest,
    TripContext,
)


def _flight(flight_id: str, origin: str, dest: str, price: float) -> FlightInventoryItem:
    return FlightInventoryItem(
        flight_id=flight_id,
        carrier="ZZ",
        flight_no=flight_id,
        origin_airport=origin,
        dest_airport=dest,
        dep_ts="2026-09-01T09:00+08:00",
        arr_ts="2026-09-01T17:00+09:00",
        duration_min=420,
        price=price,
        cabin_class="ECONOMY",
        seats_available=9,
        stops=0,
        wheelchair_assist_available=True,
        step_free_boarding=True,
    )


# SIN -> Tokyo, where Tokyo is both Narita and Haneda. The Haneda flight is the
# cheaper one, which is precisely what the old single-gateway resolution hid.
INVENTORY = [
    _flight("TO-NRT", "SIN", "NRT", 900),
    _flight("TO-HND", "SIN", "HND", 400),
    _flight("BACK-NRT", "NRT", "SIN", 900),
    _flight("BACK-HND", "HND", "SIN", 400),
    _flight("UNRELATED", "SIN", "BKK", 100),
]


def _request(dest_airports: list[str]) -> FlightProposalRequest:
    return FlightProposalRequest(
        trip_context=TripContext(
            origin_country="Singapore",
            dest_country="Japan",
            origin_city="Singapore",
            dest_city="Tokyo",
            origin_airports=["SIN"],
            dest_airports=dest_airports,
            depart_date="2026-09-01",
            return_date="2026-09-01",
            traveller_ages=[30],
        )
    )


def test_a_flight_into_either_airport_is_a_candidate():
    candidates = propose_flights(_request(["NRT", "HND"]), INVENTORY).candidates
    outbound = {c.flight_id for c in candidates if c.direction == "OUTBOUND"}
    assert outbound == {"TO-NRT", "TO-HND"}


def test_the_cheaper_airport_wins_on_price_across_the_whole_city():
    """The concrete payoff: ranking is over the city, not over one gateway."""
    candidates = propose_flights(_request(["NRT", "HND"]), INVENTORY).candidates
    outbound = [c for c in candidates if c.direction == "OUTBOUND"]
    assert outbound[0].flight_id == "TO-HND"


def test_naming_one_airport_still_excludes_the_other():
    """Widening the match must not turn every filter into a pass-through."""
    outbound = [
        c for c in propose_flights(_request(["NRT"]), INVENTORY).candidates
        if c.direction == "OUTBOUND"
    ]
    assert {c.flight_id for c in outbound} == {"TO-NRT"}


def test_unrelated_routes_are_still_filtered_out():
    candidates = propose_flights(_request(["NRT", "HND"]), INVENTORY).candidates
    assert "UNRELATED" not in {c.flight_id for c in candidates}


def test_the_return_leg_searches_the_city_too():
    candidates = propose_flights(_request(["NRT", "HND"]), INVENTORY).candidates
    inbound = {c.flight_id for c in candidates if c.direction == "RETURN"}
    assert inbound == {"BACK-NRT", "BACK-HND"}


def test_screening_covers_every_airport_in_the_city():
    """The explainability trace must account for all of them, not just NRT."""
    screened = screen_flights(_request(["NRT", "HND"]), INVENTORY)
    assert {"TO-NRT", "TO-HND"} <= {row.flight_id for row in screened}
