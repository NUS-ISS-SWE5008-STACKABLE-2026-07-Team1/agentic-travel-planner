"""Integrity checks on the shared city dataset.

These are data tests, not logic tests. `flaskapp/places.py` is hand-curated and
is consumed by three agents plus the intake form, so the failure mode worth
guarding is a bad row — a country name that does not match the form's list, a
duplicated airport, a slug that changed — rather than a bug in the four small
lookup functions.
"""

from __future__ import annotations

import re

import pytest

from flaskapp.countries import COUNTRIES
from flaskapp.places import (
    CITIES,
    KNOWN_AIRPORTS,
    airports_for,
    cities_for,
    city_by_slug,
    city_options,
    countries_with_cities,
    find_city,
)

IATA = re.compile(r"^[A-Z]{3}$")
SLUG = re.compile(r"^[a-z]{2}-[a-z0-9-]+$")


def test_every_country_key_exists_in_the_form_country_list():
    """A country name that does not match `countries.py` exactly is invisible.

    The intake form builds its country <select> from COUNTRIES and then looks
    up cities by that exact string. A typo here would not raise anywhere — the
    country would simply offer no cities, which reads as missing data rather
    than a bug. This is the test that turns it into a build failure.
    """
    unknown = sorted({city.country for city in CITIES.values()} - set(COUNTRIES))
    assert unknown == [], f"country names not present in countries.py: {unknown}"


def test_slugs_are_well_formed_and_match_their_key():
    for slug, city in CITIES.items():
        assert SLUG.match(slug), f"malformed slug: {slug}"
        assert city.slug == slug


def test_airport_codes_are_iata_and_non_empty():
    for city in CITIES.values():
        assert city.airports, f"{city.slug} has no airports"
        for code in city.airports:
            assert IATA.match(code), f"{city.slug} has non-IATA code {code!r}"


def test_no_airport_serves_two_cities():
    """An airport in two cities would make route resolution ambiguous."""
    seen: dict[str, str] = {}
    for city in CITIES.values():
        for code in city.airports:
            assert code not in seen, f"{code} claimed by {seen.get(code)} and {city.slug}"
            seen[code] = city.slug


def test_no_duplicate_city_name_within_a_country():
    """`find_city` resolves by display name, so names must be unique per country."""
    for country in countries_with_cities():
        names = [city.name.casefold() for city in cities_for(country)]
        assert len(names) == len(set(names)), f"duplicate city name in {country}"


def test_cities_are_alphabetical_within_a_country():
    """The form renders them in dataset order; keep that order predictable."""
    for country in countries_with_cities():
        names = [city.name for city in cities_for(country)]
        assert names == sorted(names), f"{country} cities are not alphabetical"


def test_primary_airport_is_the_first_listed():
    tokyo = city_by_slug("jp-tokyo")
    assert tokyo is not None
    assert tokyo.primary_airport == "NRT"
    assert tokyo.airports == ("NRT", "HND")


def test_multi_airport_cities_return_every_airport():
    """The reason city granularity exists: Haneda must not be invisible."""
    assert airports_for("Japan", "Tokyo") == ("NRT", "HND")
    assert set(airports_for("United Kingdom", "London")) >= {"LHR", "LGW"}


def test_lookup_is_case_and_whitespace_insensitive():
    assert find_city("Japan", "  tokyo ") == city_by_slug("jp-tokyo")
    assert city_by_slug("  JP-TOKYO ") == city_by_slug("jp-tokyo")


@pytest.mark.parametrize(
    "country, city",
    [("Japan", "Kyoto"), ("Nauru", "Yaren"), (None, "Tokyo"), ("Japan", None)],
)
def test_unresolvable_lookups_return_empty_rather_than_raising(country, city):
    """Unroutable is a normal answer the adapter reports, never an exception.

    Kyoto is the interesting case: a real, famous city with no commercial
    airport of its own. It must resolve to nothing rather than be quietly
    attached to Osaka's.
    """
    assert find_city(country, city) is None
    assert airports_for(country, city) == ()


def test_country_without_cities_returns_empty_tuple():
    assert cities_for("Nauru") == ()
    assert cities_for("") == ()
    assert cities_for(None) == ()


def test_known_airports_covers_the_seed_inventory_routes():
    """Seed inventory must be reachable from the form, or it is dead data.

    Every airport with seed rows has to belong to some selectable city,
    otherwise those flights can never be searched for.
    """
    from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_AIRPORTS

    unreachable = sorted(SEED_AIRPORTS - KNOWN_AIRPORTS)
    assert unreachable == [], f"seed inventory no city can reach: {unreachable}"


def test_hong_kong_is_a_city_of_china():
    """Team decision, and what makes the existing HKG inventory reachable.

    `countries.py` has no separate Hong Kong entry, so listing it as a Chinese
    city is what puts it in the destination dropdown at all.
    """
    hong_kong = city_by_slug("cn-hong-kong")
    assert hong_kong is not None
    assert hong_kong.country == "China"
    assert hong_kong.airports == ("HKG",)
    assert hong_kong in cities_for("China")


def test_city_options_is_json_serialisable_and_keyed_by_country():
    options = city_options()
    assert options["Japan"][0].keys() == {"slug", "name", "label"}
    tokyo = next(c for c in options["Japan"] if c["slug"] == "jp-tokyo")
    assert tokyo["label"] == "Tokyo (NRT/HND)"
