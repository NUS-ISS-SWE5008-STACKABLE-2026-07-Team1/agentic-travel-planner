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


def test_countries_resolve_to_airports():
    adapted = to_flight_request(_travel_request())
    context = adapted.request.trip_context
    assert (context.origin_airport, context.dest_airport) == ("SIN", "NRT")
    assert (context.origin_country, context.dest_country) == ("Singapore", "Japan")
    assert adapted.is_routable and adapted.has_inventory
    assert adapted.unresolved == []


def test_unmappable_country_is_reported_not_raised():
    """An unroutable request must degrade to zero candidates with a reason,
    never to an exception the orchestrator has to catch."""
    adapted = to_flight_request(_travel_request(destination="Chad"))
    assert not adapted.is_routable
    assert any("Chad" in note for note in adapted.unresolved)
    assert propose_flights(adapted.request, SEED_FLIGHT_INVENTORY).candidates == []


def test_routable_country_without_inventory_is_distinguished():
    """"We cannot route this" and "we can route it but have no data" are
    different answers and a traveller deserves to be told which one applies."""
    adapted = to_flight_request(_travel_request(destination="France"))
    assert adapted.is_routable
    assert not adapted.has_inventory
    assert any("No flight inventory loaded for France" in note for note in adapted.unresolved)


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


def test_every_form_country_either_resolves_or_is_explicitly_unmapped():
    """Guards against a typo'd key: any airport mapping we do have must be for
    a country the form can actually submit."""
    from flaskapp.countries import COUNTRIES
    from flaskapp.travel_ai.agents.flight_agent.airports import COUNTRY_PRIMARY_AIRPORT

    assert set(COUNTRY_PRIMARY_AIRPORT) <= set(COUNTRIES)
    assert resolve_airport("Nowhereland") is None
    assert resolve_airport(None) is None


def test_seed_backed_countries_actually_have_inventory():
    """The 'has inventory' claim must stay true as seed data changes."""
    from flaskapp.travel_ai.agents.flight_agent.airports import (
        SEED_BACKED_COUNTRIES,
        COUNTRY_PRIMARY_AIRPORT,
    )

    stocked = {item.origin_airport for item in SEED_FLIGHT_INVENTORY}
    for country in SEED_BACKED_COUNTRIES:
        assert COUNTRY_PRIMARY_AIRPORT[country] in stocked
