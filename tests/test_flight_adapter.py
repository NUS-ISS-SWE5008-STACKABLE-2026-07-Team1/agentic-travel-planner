"""Contract tests for the shared-TravelRequest -> Flight Agent translation.

These are the tests that fail first when another developer changes
`flaskapp/travel_ai/schemas.py` or the intake form, which is the point: the
adapter is the seam between two schemas owned by different people, so it is
where drift should be caught.
"""

from datetime import date

import pytest

from flaskapp.travel_ai.agents.flight_agent.adapter import (
    derive_flight_preferences,
    needs_wheelchair,
    strip_traveller_prefix,
    to_flight_request,
)
from flaskapp.travel_ai.agents.flight_agent.airports import resolve_airport
from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY
from flaskapp.travel_ai.schemas import TravelRequest

# Mirrors exactly what static/js/app.js's buildPayload() posts, including the
# "Traveler N: " prefixing it applies to the combined accessibility list.
FORM_PAYLOAD = dict(
    origin="Singapore",
    destination="Japan",
    departure_date=date(2026, 9, 1),
    return_date=date(2026, 9, 5),
    travellers=2,
    traveller_ages=[34, 8],
    traveller_genders=["female", "male"],
    traveller_accessibility_needs=[["wheelchair assistance"], []],
    budget=4000,
    currency="SGD",
    preferences=["direct flights", "local food"],
    accessibility_needs=["Traveler 1: wheelchair assistance"],
    refinement_notes=[],
)


def _travel_request(**overrides) -> TravelRequest:
    return TravelRequest(**{**FORM_PAYLOAD, **overrides})


def test_cities_resolve_to_every_serving_airport():
    """The point of city intake: Tokyo means the city, so Haneda is included."""
    adapted = to_flight_request(_travel_request(origin_city="Singapore", destination_city="Tokyo"))
    context = adapted.request.trip_context
    assert (context.origin_city, context.dest_city) == ("Singapore", "Tokyo")
    assert context.origin_airports == ["SIN"]
    assert context.dest_airports == ["NRT", "HND"]
    # The scalar stays available for display, as the primary gateway.
    assert (context.origin_airport, context.dest_airport) == ("SIN", "NRT")
    assert adapted.is_routable and adapted.has_inventory
    assert adapted.unresolved == []


def test_country_only_request_resolves_but_discloses_the_assumption():
    """A request with no city still works — it just says which city it used.

    This is the one place the old one-airport-per-country behaviour survives,
    and the difference is that it is now stated rather than silent.
    """
    adapted = to_flight_request(_travel_request())
    context = adapted.request.trip_context
    assert (context.origin_country, context.dest_country) == ("Singapore", "Japan")
    assert (context.origin_city, context.dest_city) == ("Singapore", "Tokyo")
    assert adapted.is_routable and adapted.has_inventory
    assert any("assumed Tokyo" in note for note in adapted.unresolved)


def test_unrecognised_city_falls_back_to_the_country_and_says_so():
    """Kyoto is a real city with no airport; it must not silently become Osaka."""
    adapted = to_flight_request(_travel_request(destination_city="Kyoto"))
    assert adapted.is_routable
    assert any("'Kyoto' is not a recognised destination city" in n for n in adapted.unresolved)
    assert adapted.request.trip_context.dest_city == "Tokyo"


def test_unmappable_country_is_reported_not_raised():
    """An unroutable request must degrade to zero candidates with a reason,
    never to an exception the orchestrator has to catch."""
    adapted = to_flight_request(_travel_request(destination="Chad"))
    assert not adapted.is_routable
    assert any("Chad" in note for note in adapted.unresolved)
    assert propose_flights(adapted.request, SEED_FLIGHT_INVENTORY).candidates == []


def test_routable_route_without_inventory_is_distinguished():
    """"We cannot route this" and "we can route it but have no data" are
    different answers and a traveller deserves to be told which one applies."""
    adapted = to_flight_request(_travel_request(destination="Iceland", destination_city="Reykjavik"))
    assert adapted.is_routable
    assert not adapted.has_inventory
    assert any("No flight inventory loaded for" in note for note in adapted.unresolved)
    assert any("Reykjavik" in note for note in adapted.unresolved)


def test_party_derived_from_traveller_ages():
    context = to_flight_request(_travel_request()).request.trip_context
    assert context.party == {"adults": 1, "children": 1}


def test_explicit_party_is_not_overwritten_by_derivation():
    from flaskapp.travel_ai.agents.flight_agent.schemas import TripContext

    context = TripContext(
        dest_country="Japan",
        depart_date="2026-09-01",
        return_date="2026-09-05",
        traveller_ages=[34, 8],
        party={"adults": 2, "children": 0},
    )
    assert context.party == {"adults": 2, "children": 0}


def test_traveller_genders_are_carried_for_the_bias_audit():
    """Gender has no legitimate use in flight ranking, but it is carried
    deliberately so an XRAI audit can vary it and observe the LLM. The
    invariants that make that safe live in test_flight_bias_audit.py."""
    context = to_flight_request(_travel_request()).request.trip_context
    assert context.traveller_genders == ["female", "male"]


@pytest.mark.parametrize(
    "need,expected",
    [
        ("Traveler 1: wheelchair assistance", "wheelchair assistance"),
        ("Traveler 12: step-free access", "step-free access"),
        ("step-free access", "step-free access"),
    ],
)
def test_traveller_prefix_is_stripped(need, expected):
    assert strip_traveller_prefix(need) == expected


@pytest.mark.parametrize(
    "needs",
    [["wheelchair"], ["wheelchair assistance"], ["Wheelchair user"], ["step-free", "needs wheelchair"]],
)
def test_free_text_wheelchair_needs_are_detected(needs):
    """The form takes free text, so an exact match on the bare token missed
    almost every real entry — disabling the accessibility filter for exactly
    the travellers it protects."""
    assert needs_wheelchair(needs)


def test_unrelated_needs_do_not_trigger_wheelchair_filter():
    assert not needs_wheelchair(["step-free access", "quiet cabin"])


def test_accessibility_needs_reach_domain_in_matchable_form():
    """End-to-end: a prefixed, free-text need from the form must still engage
    the wheelchair filter once it has been through the adapter."""
    from flaskapp.travel_ai.agents.flight_agent.domain import needs_wheelchair as domain_check

    context = to_flight_request(_travel_request()).request.trip_context
    assert domain_check(context.accessibility_needs)


def test_direct_flight_preference_derived_from_free_text():
    context = to_flight_request(_travel_request()).request.trip_context
    assert context.flight_preferences.prefer_direct
    assert not context.flight_preferences.avoid_red_eye


def test_derivation_only_fires_on_unambiguous_phrases():
    assert not derive_flight_preferences(["local food", "quiet hotel"]).prefer_direct
    assert derive_flight_preferences(["no red-eye flights"]).avoid_red_eye


def test_currency_and_budget_carry_through():
    context = to_flight_request(_travel_request()).request.trip_context
    assert (context.budget_total, context.currency) == (4000, "SGD")


def test_refinement_notes_carry_through():
    notes = ["Prefer a quieter hotel near a train station"]
    context = to_flight_request(_travel_request(refinement_notes=notes)).request.trip_context
    assert context.refinement_notes == notes


def test_passport_country_comes_from_the_user_profile():
    """The trip form does not collect it; the signed-in user's country does."""
    adapted = to_flight_request(_travel_request(), passport_country="Singapore")
    assert adapted.request.trip_context.passport_country == "Singapore"
    assert to_flight_request(_travel_request()).request.trip_context.passport_country is None


def test_every_primary_city_is_a_country_the_form_can_submit():
    """Guards against a typo'd key: any fallback mapping we have must be for a
    country the form can actually submit, and must name a real city."""
    from flaskapp.countries import COUNTRIES
    from flaskapp.places import CITIES, PRIMARY_CITY

    assert set(PRIMARY_CITY) <= set(COUNTRIES)
    assert set(PRIMARY_CITY.values()) <= set(CITIES)
    assert resolve_airport("Nowhereland") is None
    assert resolve_airport(None) is None


def test_the_has_inventory_claim_is_derived_from_the_dataset():
    """The 'has inventory' claim must stay true as seed data changes.

    It is now computed from the rows rather than asserted by a hand-maintained
    country list, so this checks the derivation agrees with the raw data
    instead of checking that two lists were edited in lockstep.
    """
    from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_ROUTES, covers_route

    stocked_pairs = {(item.origin_airport, item.dest_airport) for item in SEED_FLIGHT_INVENTORY}
    assert SEED_ROUTES == stocked_pairs
    assert covers_route(["SIN"], ["NRT"])
    # Any one stocked pair is enough, even when other pairs for the city are not.
    # KEF has no seed rows, so this passes only on the strength of NRT.
    assert covers_route(["SIN"], ["NRT", "KEF"])
    assert not covers_route(["SIN"], ["KEF"])
    assert not covers_route([], ["NRT"])
