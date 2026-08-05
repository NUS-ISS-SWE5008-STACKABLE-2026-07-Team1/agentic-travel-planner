"""Country -> primary international airport resolution.

Why this exists: the intake form (`templates/main.html`) offers a country
`<select>` built from `flaskapp/countries.py`, so the shared `TravelRequest`
carries `origin="Singapore"` / `destination="Japan"`. Flight Agent's inventory
and every filter in `domain.py` are keyed on IATA airport codes. Something has
to bridge the two, and this is deliberately that one small, reviewable place
rather than a lookup scattered through domain logic.

Two honest limitations, both intentional and both visible to callers:

1. **One airport per country.** A country with several international gateways
   is reduced to its busiest one. That is wrong for the traveller who wants
   Osaka rather than Tokyo, and it is why `dest_country` is retained on
   `TripContext` alongside the resolved airport instead of being replaced by
   it. Collecting a city or airport at intake is the real fix; this is the
   shim until the form offers that.
2. **Partial coverage.** Unmapped countries resolve to `None`, which flows
   through to an empty candidate list — the truthful "no inventory for this
   route" answer rather than a crash or a silently wrong airport.

`SEED_BACKED_COUNTRIES` is the subset that actually has flights in
`seed_data.py`. Everything else resolves to a real airport code but has no
inventory behind it yet, so it will legitimately return no candidates until
live supplier data is wired in.
"""

from __future__ import annotations

# Countries whose resolved airport has rows in SEED_FLIGHT_INVENTORY today.
# Hong Kong (HKG) is present in the seed data but absent from countries.py's
# list, so it is not selectable from the form and is not listed here.
SEED_BACKED_COUNTRIES: frozenset[str] = frozenset(
    {"Singapore", "Japan", "United Kingdom", "Australia", "Thailand"}
)

# Primary international gateway per country. Keys must match countries.py
# exactly (that file is the form's source of truth). This is a working subset,
# not a complete reference dataset — extend it as routes are added, and
# replace it wholesale once a real airports dataset is available.
COUNTRY_PRIMARY_AIRPORT: dict[str, str] = {
    # --- Backed by seed inventory ---
    "Singapore": "SIN",
    "Japan": "NRT",
    "United Kingdom": "LHR",
    "Australia": "SYD",
    "Thailand": "BKK",
    # --- Resolvable, but no inventory behind them yet ---
    "China": "PEK",
    "France": "CDG",
    "Germany": "FRA",
    "India": "DEL",
    "Indonesia": "CGK",
    "Italy": "FCO",
    "Korea, South": "ICN",
    "Malaysia": "KUL",
    "Netherlands": "AMS",
    "New Zealand": "AKL",
    "Philippines": "MNL",
    "Qatar": "DOH",
    "Spain": "MAD",
    "Switzerland": "ZRH",
    "Taiwan": "TPE",
    "Türkiye": "IST",
    "United Arab Emirates": "DXB",
    "United States": "JFK",
    "Vietnam": "SGN",
}


def resolve_airport(country: str | None) -> str | None:
    """The primary airport for `country`, or None when it cannot be resolved.

    None is a normal outcome, not an error: the caller records it as the reason
    no candidates were produced (see `adapter.py`'s `unresolved` list), which
    keeps an unmapped country explainable instead of looking like a bug.
    """
    if not country:
        return None
    return COUNTRY_PRIMARY_AIRPORT.get(country.strip())


def has_seed_inventory(country: str | None) -> bool:
    """Whether this country has seed flights today.

    Lets a caller distinguish "we cannot route this at all" from "we can route
    it, but no inventory has been loaded yet" — a distinction worth surfacing
    to a traveller, and worth logging separately.
    """
    return bool(country) and country.strip() in SEED_BACKED_COUNTRIES
