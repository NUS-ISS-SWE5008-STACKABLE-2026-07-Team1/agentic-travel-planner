"""Intake must produce a destination the hotel adapter can actually resolve.

Reported as "hotel_transport_agent is never called". It was called on every run;
it produced no options. Intake put a CITY in `destination`, which the hotel
adapter reads as a country (`adapter.py:73`), and `find_city` is scoped by
country — so `find_city('Tokyo', ...)` and `primary_city('Tokyo')` both miss,
the agent falls back to the prompt-only path, and that path strips invented
properties by design. The seed inventory had 69 hotels including `jp-tokyo` the
whole time; the lookup never reached them.
"""

from flaskapp.travel_ai.agents.hotel_transport_agent.adapter import to_hotel_trip_context
from flaskapp.travel_ai.agents.orchestrator_agent.intake import compute_gaps, to_request_payload
from flaskapp.travel_ai.agents.orchestrator_agent.intake_schemas import ExtractedIntent
from flaskapp.travel_ai.schemas import TravelRequest

COMPLETE = {
    "origin": "Singapore", "destination": "Japan", "destination_city": "Tokyo",
    "departure_date": "2026-10-10", "return_date": "2026-10-24",
    "travellers": 1, "budget": 3000.0, "currency": "SGD",
    "traveller_ages": [34], "traveller_genders": ["male"],
    "traveller_accessibility_needs": [[]],
}


def keys(missing):
    return [field.key for field in missing]


def test_destination_city_is_asked_for():
    """The gap question that makes the country/city split real."""
    assert "destination_city" in keys(compute_gaps(ExtractedIntent()))


def test_a_country_without_a_city_is_still_incomplete():
    intent = ExtractedIntent.model_validate({**COMPLETE, "destination_city": None})
    assert "destination_city" in keys(compute_gaps(intent))


def test_a_complete_intent_carries_the_city_into_the_request():
    payload = to_request_payload(ExtractedIntent.model_validate(COMPLETE))
    assert payload["destination"] == "Japan"
    assert payload["destination_city"] == "Tokyo"


def test_the_hotel_adapter_resolves_an_intake_built_request():
    """The regression. Before the fix this reported no airport/city mapping."""
    payload = to_request_payload(ExtractedIntent.model_validate(COMPLETE))
    context, unresolved = to_hotel_trip_context(TravelRequest.model_validate(payload))
    assert context.dest_city_slug == "jp-tokyo"
    assert not any("No airport/city mapping" in note for note in unresolved)


def test_the_old_intake_shape_is_what_broke_it():
    """Characterises the bug: a city in `destination` resolves to nothing."""
    broken = TravelRequest.model_validate({
        **to_request_payload(ExtractedIntent.model_validate(COMPLETE)),
        "destination": "Tokyo", "destination_city": None,
    })
    context, unresolved = to_hotel_trip_context(broken)
    assert context.dest_city_slug is None
    assert any("No airport/city mapping" in note for note in unresolved)
