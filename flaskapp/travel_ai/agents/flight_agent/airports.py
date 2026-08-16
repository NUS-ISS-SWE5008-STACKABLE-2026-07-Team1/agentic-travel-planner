"""Country + city -> airport resolution for Flight Agent.

Why this exists: the intake form collects a country and a city
(`templates/main.html`), while Flight Agent's inventory and every filter in
`domain.py` are keyed on IATA airport codes. Something has to bridge the two,
and this is deliberately that one small, reviewable place rather than a lookup
scattered through domain logic. The dataset itself lives in `flaskapp/places.py`
because Hotel & Transport keys its inventory on the same cities; this module
holds only the resolution *policy*.

This file previously mapped one airport per country, and its own docstring
called that out as the thing to fix. Both of the limitations it apologised for
are now gone:

1. **Multi-airport cities are honoured.** `resolve_route` returns every airport
   serving the chosen city, ordered primary-first. Choosing Tokyo surfaces
   Haneda fares alongside Narita instead of silently meaning NRT.
2. **Country-only requests are explicit, not silent.** An API caller that sends
   no city still resolves — to that country's `PRIMARY_CITY` — but the
   substitution comes back as a note the traveller is told about, rather than
   an invisible narrowing.

What has NOT changed: nothing here raises. An unresolvable route yields empty
airport tuples plus a human-readable reason, which flows to
`AgentFinding.warnings` and reads as a coverage gap rather than a crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flaskapp import places


@dataclass
class ResolvedRoute:
    """Airports for one end of a trip, plus anything the traveller should know.

    `notes` uses the same currency as `AdaptedRequest.unresolved` and
    `InventoryResult.notes` — plain strings the agent appends to its warnings.
    """

    airports: tuple[str, ...] = ()
    city: places.City | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def primary(self) -> str | None:
        """The main gateway, for display and for scalar-only callers."""
        return self.airports[0] if self.airports else None

    @property
    def is_resolved(self) -> bool:
        return bool(self.airports)


def resolve_route(country: str | None, city_name: str | None, *, label: str) -> ResolvedRoute:
    """Resolve one end of a trip to the airports that serve it.

    `label` is the human word for this end ("departure" / "destination") and is
    used only to phrase notes, so a warning names which leg failed.

    Three outcomes, each a normal result:

    - City resolves -> its airports, no note.
    - Only a country -> the primary city's airports, WITH a note disclosing the
      assumption. Never silent: the traveller asked for a country and is being
      given one specific city's gateways.
    - Neither resolves -> empty airports and a note saying which input failed.
    """
    notes: list[str] = []

    if city_name:
        city = places.find_city(country, city_name)
        if city:
            return ResolvedRoute(airports=city.airports, city=city)
        notes.append(
            f"{city_name!r} is not a recognised {label} city in {country or 'the given country'}; "
            f"falling back to the country's main gateway."
        )

    fallback = places.primary_city(country)
    if fallback is None:
        notes.append(
            f"No airport mapping for {label} {country!r}."
            if country
            else f"No {label} country was given."
        )
        return ResolvedRoute(notes=notes)

    if not city_name:
        notes.append(
            f"No {label} city was given for {country}; assumed "
            f"{fallback.name} ({'/'.join(fallback.airports)})."
        )
    else:
        notes.append(f"Assumed {fallback.name} ({'/'.join(fallback.airports)}).")

    return ResolvedRoute(airports=fallback.airports, city=fallback, notes=notes)


def resolve_airport(country: str | None) -> str | None:
    """The primary airport for a country, or None.

    Retained for callers that predate city intake and only have a country. New
    code should use `resolve_route`, which reports its assumptions instead of
    hiding them behind a single code.
    """
    city = places.primary_city(country)
    return city.primary_airport if city else None
