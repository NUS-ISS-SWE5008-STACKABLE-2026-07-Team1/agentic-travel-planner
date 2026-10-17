"""Duffel offer -> FlightInventoryItem mapping, and fail-soft error handling.

Entirely offline: every test drives a stub session or the mapping functions
directly against tests/fixtures/duffel_offer_request.json. No token, no
network, so this suite is meaningful in CI where neither exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from flaskapp.travel_ai.agents.flight_agent.providers import get_inventory_provider
from flaskapp.travel_ai.agents.flight_agent.providers.duffel import (
    ACCESSIBILITY_NOTE,
    SEATS_NOTE,
    DuffelInventoryProvider,
    build_payload,
    localise,
    offers_to_inventory,
    parse_iso_duration,
)
from flaskapp.travel_ai.agents.flight_agent.providers.seed import SeedInventoryProvider
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightProposalRequest,
    TripContext,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "duffel_offer_request.json").read_text(encoding="utf-8")
)


def _request(ages=(34, 8)) -> FlightProposalRequest:
    return FlightProposalRequest(
        trip_context=TripContext(
            origin_country="United Kingdom",
            dest_country="United States",
            origin_airport="LHR",
            dest_airport="JFK",
            depart_date="2026-09-01",
            return_date="2026-09-05",
            traveller_ages=list(ages),
            currency="GBP",
        )
    )


class _StubResponse:
    def __init__(self, status_code=200, payload=None, body_is_json=True):
        self.status_code = status_code
        self._payload = payload
        self._body_is_json = body_is_json

    def json(self):
        if not self._body_is_json:
            raise ValueError("not json")
        return self._payload


class _StubSession:
    """Captures the outbound call so the request body can be asserted on."""

    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.raises is not None:
            raise self.raises
        return self.response


def _provider(session) -> DuffelInventoryProvider:
    return DuffelInventoryProvider(token="duffel_test_notreal", session=session)


# --- Duration parsing -------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PT7H10M", 430),
        ("PT45M", 45),
        ("PT2H", 120),
        ("P1DT2H5M", 1565),
        ("", None),
        (None, None),
        ("garbage", None),
        ("PT0M", None),  # zero is not a usable duration_min (schema requires gt=0)
    ],
)
def test_parse_iso_duration(value, expected):
    assert parse_iso_duration(value) == expected


# --- Timezone recombination -------------------------------------------------

def test_localise_attaches_the_places_utc_offset():
    """Duffel sends local-but-naive times with the zone on the place object.
    `domain._is_red_eye` and check-in feasibility both read the offset, so the
    two have to be recombined rather than passed through naive."""
    assert localise("2026-09-01T08:00:00", {"time_zone": "Europe/London"}) == "2026-09-01T08:00:00+01:00"
    assert localise("2026-09-01T11:10:00", {"time_zone": "America/New_York"}) == "2026-09-01T11:10:00-04:00"


def test_localise_degrades_to_naive_rather_than_guessing():
    """An unresolvable zone must not produce a wrong offset. Naive is
    recoverable (local-clock comparisons still hold); a guessed offset silently
    corrupts every arrival-time constraint downstream."""
    assert localise("2026-09-01T08:00:00", {}) == "2026-09-01T08:00:00"
    assert localise("2026-09-01T08:00:00", {"time_zone": "Not/AZone"}) == "2026-09-01T08:00:00"
    assert localise("2026-09-01T08:00:00", None) == "2026-09-01T08:00:00"


def test_localise_leaves_an_already_offset_timestamp_alone():
    assert localise("2026-09-01T08:00:00+01:00", {"time_zone": "America/New_York"}) == (
        "2026-09-01T08:00:00+01:00"
    )


# --- Offer mapping ----------------------------------------------------------

def test_each_offer_becomes_one_item_per_slice():
    items = offers_to_inventory(FIXTURE, passenger_count=2)
    assert len(items) == 4  # 2 offers x 2 slices
    assert [i.flight_id for i in items] == [
        "off_0000AfakeDirect:0",
        "off_0000AfakeDirect:1",
        "off_0000AfakeConnect:0",
        "off_0000AfakeConnect:1",
    ]


def test_price_is_divided_across_passengers_and_legs():
    """domain._effective_cost multiplies price by party size and ranks per leg,
    so Duffel's whole-trip total has to be divided by both."""
    items = offers_to_inventory(FIXTURE, passenger_count=2)
    assert items[0].price == pytest.approx(1200.00 / 2 / 2)  # 300.00
    assert items[2].price == pytest.approx(880.00 / 2 / 2)  # 220.00


def test_timestamps_span_first_departure_to_last_arrival_with_offsets():
    items = offers_to_inventory(FIXTURE, passenger_count=2)
    connecting_outbound = items[2]
    assert connecting_outbound.dep_ts == "2026-09-01T09:30:00+01:00"  # LHR, first segment
    assert connecting_outbound.arr_ts == "2026-09-01T15:15:00-04:00"  # JFK, last segment


def test_stops_come_from_segment_count():
    items = offers_to_inventory(FIXTURE, passenger_count=2)
    assert items[0].stops == 0  # direct
    assert items[2].stops == 1  # LHR-BOS-JFK


def test_duration_and_cabin_class_are_read_from_the_slice():
    items = offers_to_inventory(FIXTURE, passenger_count=2)
    assert items[0].duration_min == 430  # PT7H10M
    assert items[2].duration_min == 585  # PT9H45M
    assert items[0].cabin_class == "ECONOMY"
    assert items[2].cabin_class == "PREMIUM_ECONOMY"


def test_carrier_and_flight_number_come_from_the_marketing_carrier():
    items = offers_to_inventory(FIXTURE, passenger_count=2)
    assert items[0].carrier == "ZZ"
    assert items[0].flight_no == "ZZ4321"


def test_accessibility_is_unknown_never_fabricated():
    """The whole point of the nullable fields. Duffel publishes neither, and
    defaulting either to True would invent an accessibility guarantee."""
    items = offers_to_inventory(FIXTURE, passenger_count=2)
    assert all(i.wheelchair_assist_available is None for i in items)
    assert all(i.step_free_boarding is None for i in items)
    assert all(i.seat_inventory is None for i in items)


def test_rows_are_labelled_with_their_source():
    items = offers_to_inventory(FIXTURE, passenger_count=2)
    assert all(i.source == "duffel" for i in items)


def test_seats_available_defaults_to_party_size():
    """Duffel publishes no seat count. Party size keeps domain._screen_item's
    seat check from excluding every live option on data we do not have."""
    items = offers_to_inventory(FIXTURE, passenger_count=2)
    assert all(i.seats_available == 2 for i in items)


def test_slices_without_segments_are_dropped_not_crashed_on():
    payload = {"data": {"offers": [{"id": "off_x", "total_amount": "100", "slices": [{"segments": []}]}]}}
    assert offers_to_inventory(payload, passenger_count=1) == []


def test_empty_or_malformed_payloads_map_to_nothing():
    assert offers_to_inventory({}, passenger_count=1) == []
    assert offers_to_inventory({"data": {}}, passenger_count=1) == []
    assert offers_to_inventory(None, passenger_count=1) == []


# --- Request building -------------------------------------------------------

def test_payload_carries_both_legs_and_one_passenger_per_age():
    payload = build_payload(_request(ages=(34, 8, 71)), supplier_timeout_ms=15000)["data"]
    assert [(s["origin"], s["destination"], s["departure_date"]) for s in payload["slices"]] == [
        ("LHR", "JFK", "2026-09-01"),
        ("JFK", "LHR", "2026-09-05"),
    ]
    assert payload["passengers"] == [{"age": 34}, {"age": 8}, {"age": 71}]
    assert payload["supplier_timeout"] == 15000


def test_payload_falls_back_to_a_single_adult_when_no_ages_recorded():
    payload = build_payload(_request(ages=()), supplier_timeout_ms=20000)["data"]
    assert len(payload["passengers"]) == 1


def test_fetch_sends_the_version_header_and_return_offers():
    session = _StubSession(response=_StubResponse(payload=FIXTURE))
    _provider(session).fetch(_request())
    call = session.calls[0]
    assert call["headers"]["Duffel-Version"] == "v2"
    assert call["headers"]["Authorization"] == "Bearer duffel_test_notreal"
    assert call["params"] == {"return_offers": "true"}


# --- Fail-soft --------------------------------------------------------------

def test_a_successful_fetch_notes_what_the_feed_could_not_supply():
    result = _provider(_StubSession(response=_StubResponse(payload=FIXTURE))).fetch(_request())
    assert len(result.items) == 4
    assert SEATS_NOTE in result.notes
    assert ACCESSIBILITY_NOTE in result.notes


def test_network_failure_returns_an_empty_result_not_an_exception():
    """agent.py degrades to the prompt-only path on an empty result. Raising
    here would instead fail the whole graph node on a supplier hiccup."""
    session = _StubSession(raises=requests.ConnectTimeout("boom"))
    result = _provider(session).fetch(_request())
    assert result.items == []
    assert "ConnectTimeout" in result.notes[0]


def test_error_notes_never_leak_the_exception_message():
    """requests embeds the full URL — and therefore anything in it — in its
    error strings, and these notes are traveller-visible."""
    session = _StubSession(raises=requests.ConnectionError("https://api.duffel.com secret-detail"))
    note = _provider(session).fetch(_request()).notes[0]
    assert "secret-detail" not in note and "duffel_test_notreal" not in note


@pytest.mark.parametrize("status", [400, 401, 422, 500])
def test_http_errors_return_an_empty_result_with_the_status(status):
    session = _StubSession(response=_StubResponse(status_code=status))
    result = _provider(session).fetch(_request())
    assert result.items == []
    assert str(status) in result.notes[0]


def test_unreadable_body_returns_an_empty_result():
    session = _StubSession(response=_StubResponse(body_is_json=False))
    result = _provider(session).fetch(_request())
    assert result.items == []
    assert "unreadable" in result.notes[0]


def test_zero_offers_is_reported_as_a_note_not_a_failure():
    session = _StubSession(response=_StubResponse(payload={"data": {"offers": []}}))
    result = _provider(session).fetch(_request())
    assert result.items == []
    assert "no offers" in result.notes[0]


def test_max_offers_caps_the_returned_rows():
    provider = DuffelInventoryProvider(
        token="duffel_test_notreal",
        max_offers=2,
        session=_StubSession(response=_StubResponse(payload=FIXTURE)),
    )
    assert len(provider.fetch(_request()).items) == 2


def test_an_unroutable_request_never_reaches_the_network():
    """`dest_airports` is the field of record for routing, not the scalar.

    The scalar `dest_airport` is a derived display value; clearing the LIST is
    what makes a request unroutable now that a city can resolve to several
    airports.
    """
    session = _StubSession(response=_StubResponse(payload=FIXTURE))
    request = _request()
    request.trip_context.dest_airports = []
    request.trip_context.dest_airport = None
    result = _provider(session).fetch(request)
    assert result.items == [] and session.calls == []


# --- Provider selection -----------------------------------------------------

def test_seed_is_the_default_provider():
    provider, note = get_inventory_provider({})
    assert isinstance(provider, SeedInventoryProvider) and note is None


def test_duffel_without_a_token_falls_back_to_seed_but_says_so():
    """A missing credential should degrade the data source, not take the
    planner down — and must not do it silently."""
    provider, note = get_inventory_provider({"FLIGHT_INVENTORY_SOURCE": "duffel"})
    assert isinstance(provider, SeedInventoryProvider)
    assert "DUFFEL_API_TOKEN" in note


def test_duffel_is_selected_when_configured_with_a_token():
    provider, note = get_inventory_provider(
        {"FLIGHT_INVENTORY_SOURCE": "duffel", "DUFFEL_API_TOKEN": "duffel_test_notreal"}
    )
    assert isinstance(provider, DuffelInventoryProvider)
    assert provider.name == "duffel" and note is None


def test_an_unknown_source_falls_back_to_seed_with_a_note():
    provider, note = get_inventory_provider({"FLIGHT_INVENTORY_SOURCE": "sabre"})
    assert isinstance(provider, SeedInventoryProvider) and "sabre" in note


def test_seed_provider_returns_the_dataset_unchanged():
    """The golden scenarios are pinned to this dataset's exact contents, so the
    wrapper must not filter, sort or copy-modify anything."""
    from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY

    assert SeedInventoryProvider().fetch(_request()).items == list(SEED_FLIGHT_INVENTORY)
