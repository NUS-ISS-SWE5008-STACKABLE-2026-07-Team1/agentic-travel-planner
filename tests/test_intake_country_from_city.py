"""A traveller who names a city has already named the country.

Intake collects `destination` (the COUNTRY) and `destination_city` separately,
so "I want to go to Tokyo" filled the city, left the country None, and the card
then asked "Destination country" about a city whose country `places.py` already
knows. The question was answerable from data we held.

Resolution fills only what is MISSING. A country the traveller stated is never
overwritten, even when it disagrees with the city: rewriting "Tokyo, China" to
Japan hides their mistake, where leaving it lets the flight adapter report no
route — a question the orchestrator can negotiate around.
"""

import pytest

from flaskapp.places import CITIES, city_by_name
from flaskapp.travel_ai.agents.orchestrator_agent.intake import (
    compute_gaps, resolve_place_countries,
)
from flaskapp.travel_ai.agents.orchestrator_agent.intake_schemas import ExtractedIntent


# --- the reverse lookup -----------------------------------------------------

def test_a_city_resolves_to_its_country():
    city = city_by_name("Tokyo")
    assert city is not None
    assert city.country == "Japan"
    assert city.slug == "jp-tokyo"


@pytest.mark.parametrize("written", ["tokyo", "TOKYO", "  Tokyo  "])
def test_the_lookup_is_case_and_space_insensitive(written):
    """A traveller types prose, not a canonical name."""
    assert city_by_name(written).slug == "jp-tokyo"


def test_an_unknown_city_resolves_to_nothing():
    assert city_by_name("Atlantis") is None
    assert city_by_name("") is None
    assert city_by_name(None) is None


def test_an_ambiguous_name_resolves_to_nothing():
    """Every name in today's dataset is unique, so this is asserted against a
    synthetic collision rather than real data.

    The guard matters because the dataset grows and real collisions are common
    — Springfield, San Jose, Tripoli. Code that assumed uniqueness would work
    today and silently pick a country at random later.
    """
    from flaskapp.places import City

    one = City(slug="zz-springfield", name="Springfield",
               country="Testland", airports=("ZZA",))
    two = City(slug="yy-springfield", name="Springfield",
               country="Otherland", airports=("YYA",))
    patched = {**CITIES, one.slug: one, two.slug: two}
    import flaskapp.places as places
    original = places.CITIES
    try:
        places.CITIES = patched
        assert places.city_by_name("Springfield") is None
        # and an unambiguous name still resolves while the collision exists
        assert places.city_by_name("Tokyo").country == "Japan"
    finally:
        places.CITIES = original


def test_today_every_city_name_is_unique():
    """Documents the property the feature currently relies on. If this fails,
    the dataset gained a collision and some traveller will now be asked for a
    country they thought they had given — which is correct, not a regression."""
    names = [c.name.strip().casefold() for c in CITIES.values()]
    assert len(names) == len(set(names))


# --- resolution on the intent ----------------------------------------------

def test_the_destination_country_is_filled_from_the_city():
    resolved = resolve_place_countries(ExtractedIntent(destination_city="Tokyo"))
    assert resolved.destination == "Japan"
    assert resolved.destination_city == "Tokyo"


def test_only_the_destination_direction_exists_today():
    """The conversational intake collects no departure city, so there is nothing
    to derive an origin country from.

    Asserted rather than left implicit: if `origin_city` is added to
    `ExtractedIntent` later, this test is the reminder that
    `_CITY_TO_COUNTRY` should gain the second pair.
    """
    assert "origin_city" not in ExtractedIntent.model_fields


def test_a_stated_country_is_never_overwritten():
    """Even when it contradicts the city. See the module docstring."""
    resolved = resolve_place_countries(
        ExtractedIntent(destination="China", destination_city="Tokyo")
    )
    assert resolved.destination == "China"


def test_an_unknown_city_leaves_the_country_missing():
    resolved = resolve_place_countries(ExtractedIntent(destination_city="Atlantis"))
    assert resolved.destination is None


def test_resolution_does_not_mutate_the_intent_it_was_given():
    """The stored intent is the record of what the traveller said. Derivation
    returns a copy so the two never get confused."""
    original = ExtractedIntent(destination_city="Tokyo")
    resolve_place_countries(original)
    assert original.destination is None


# --- the question actually disappears --------------------------------------

def test_naming_only_a_city_stops_the_country_being_asked():
    """The behaviour the traveller sees."""
    before = {f.name for f in compute_gaps(ExtractedIntent(destination_city="Tokyo"))}
    assert "destination" in before

    after = {f.name for f in compute_gaps(
        resolve_place_countries(ExtractedIntent(destination_city="Tokyo"))
    )}
    assert "destination" not in after
    assert "destination_city" not in after


def test_an_unknown_city_still_asks_for_the_country():
    gaps = {f.name for f in compute_gaps(
        resolve_place_countries(ExtractedIntent(destination_city="Atlantis"))
    )}
    assert "destination" in gaps


# --- the API path -----------------------------------------------------------

def test_the_endpoint_resolves_before_asking_or_building():
    """`_intent_response` is the single place that both computes the gap list
    and builds the TravelRequest payload, so resolution has to happen there —
    not before saving, which would write a derived value into the record of
    what the traveller said."""
    from pathlib import Path

    source = Path("flaskapp/travel_ai/api.py").read_text(encoding="utf-8")
    body = source[source.index("def _intent_response("):]
    body = body[:body.index("\n\n\n")] if "\n\n\n" in body else body
    resolve_at = body.index("resolve_place_countries(extracted)")
    gaps_at = body.index("compute_gaps(extracted)")
    payload_at = body.index("to_request_payload(extracted)")
    assert resolve_at < gaps_at, "resolution must precede the gap list"
    assert resolve_at < payload_at, "resolution must precede the payload"
