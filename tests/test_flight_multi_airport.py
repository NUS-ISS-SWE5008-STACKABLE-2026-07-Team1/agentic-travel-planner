"""Multi-airport cities: the behaviour city intake exists to enable.

Country granularity forced one gateway per country, so a Tokyo trip silently
meant Narita and a Haneda fare could never be shown, however cheap. These tests
pin the fix at both layers that had to change — route matching in `domain.py`
and search fan-out in the Duffel provider.
"""

from __future__ import annotations

import pytest

from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights, screen_flights
from flaskapp.travel_ai.agents.flight_agent.providers.duffel import (
    MAX_AIRPORTS_PER_CITY,
    DuffelInventoryProvider,
    build_payload,
)
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


# --- Duffel fan-out ---------------------------------------------------------


class _StubResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _RecordingSession:
    """Captures the origin/destination of every search that is issued."""

    def __init__(self) -> None:
        self.searches: list[tuple[str, str]] = []

    def post(self, url, params=None, json=None, headers=None, timeout=None):
        first_slice = json["data"]["slices"][0]
        self.searches.append((first_slice["origin"], first_slice["destination"]))
        return _StubResponse({"data": {"offers": []}})


def _provider(session) -> DuffelInventoryProvider:
    return DuffelInventoryProvider(token="duffel_test_x", session=session)


def test_every_airport_pair_is_searched():
    session = _RecordingSession()
    _provider(session).fetch(_request(["NRT", "HND"]))
    assert session.searches == [("SIN", "NRT"), ("SIN", "HND")]


def test_pairs_are_capped_and_the_cap_is_disclosed():
    """London has four airports; unbounded fan-out would bill four searches."""
    request = _request(["NRT", "HND"])
    request.trip_context.origin_airports = ["LHR", "LGW", "STN", "LTN"]
    session = _RecordingSession()
    result = _provider(session).fetch(request)

    assert len(session.searches) == MAX_AIRPORTS_PER_CITY * MAX_AIRPORTS_PER_CITY
    assert any("only the 2 main airports" in note for note in result.notes)


def test_a_repeated_per_pair_note_is_only_told_once():
    session = _RecordingSession()
    result = _provider(session).fetch(_request(["NRT", "HND"]))
    no_offer_notes = [n for n in result.notes if "returned no offers" in n]
    assert len(no_offer_notes) == len(set(no_offer_notes))


@pytest.mark.parametrize("origin, dest", [("SIN", "HND"), ("LHR", "NRT")])
def test_build_payload_searches_the_pair_it_is_given(origin, dest):
    payload = build_payload(
        _request(["NRT", "HND"]), supplier_timeout_ms=20000, origin=origin, dest=dest
    )
    outbound, inbound = payload["data"]["slices"]
    assert (outbound["origin"], outbound["destination"]) == (origin, dest)
    # The return leg must mirror the same pair, not fall back to the primary.
    assert (inbound["origin"], inbound["destination"]) == (dest, origin)
