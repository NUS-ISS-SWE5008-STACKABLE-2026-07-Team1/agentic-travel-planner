"""Translation between the shared `TravelRequest` and Hotel & Transport Agent's contracts.

This is the seam. `flaskapp/travel_ai/schemas.py` is team-shared and changes as
other agents are developed; Hotel & Transport Agent's own schemas are tuned to
hotel/transport domain logic and should not be dragged along with every
shared-schema edit. Everything that knows about BOTH lives here, so a change
to either side is a change to one file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flaskapp.places import find_city
from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import (
    HotelConstraints,
    HotelPreferences,
    HotelProposalRequest,
    HotelTripContext,
)
from flaskapp.travel_ai.agents.hotel_transport_agent.seed_data import (
    covers_hotels,
)

_WHEELCHAIR_HINTS = ("wheelchair",)


@dataclass
class AdaptedRequest:
    """A `HotelProposalRequest` plus what could not be resolved building it."""

    request: HotelProposalRequest
    unresolved: list[str] = field(default_factory=list)

    @property
    def is_routable(self) -> bool:
        """Whether a destination city/slug was resolved."""
        ctx = self.request.trip_context
        return bool(ctx.dest_city_slug or ctx.dest_city)

    @property
    def has_inventory(self) -> bool:
        ctx = self.request.trip_context
        slugs = [ctx.dest_city_slug] if ctx.dest_city_slug else []
        if ctx.dest_city and not ctx.dest_city_slug:
            city = find_city(ctx.dest_country, ctx.dest_city)
            if city:
                slugs.append(city.slug)
        return self.is_routable and covers_hotels(slugs)


def _needs_wheelchair(accessibility_needs: list[str]) -> bool:
    return any(
        hint in need.lower()
        for need in accessibility_needs
        for hint in _WHEELCHAIR_HINTS
    )


def derive_hotel_preferences(preferences: list[str]) -> HotelPreferences:
    """Best-effort structured preferences from free-text list."""
    lowered = [p.lower() for p in preferences]
    joined = " | ".join(lowered)
    return HotelPreferences(
        prefer_center=any(phrase in joined for phrase in ("city centre", "city center", "central", "downtown")),
    )


def to_hotel_trip_context(travel_request, *, arrival_airport: str | None = None) -> tuple[HotelTripContext, list[str]]:
    """Build a `HotelTripContext` from a shared `TravelRequest`."""
    unresolved: list[str] = []

    dest_country = travel_request.destination
    dest_city = getattr(travel_request, "destination_city", None)
    dest_city_slug = None
    dest_airports: list[str] = []

    if dest_city:
        city = find_city(dest_country, dest_city)
        if city:
            dest_city_slug = city.slug
            dest_airports = list(city.airports)
        else:
            unresolved.append(
                f"'{dest_city}' is not a recognised city in {dest_country}; "
                f"hotel search will use the country-level fallback."
            )

    # Fall back to primary city if no city was chosen
    if not dest_city_slug:
        from flaskapp.places import primary_city
        fallback = primary_city(dest_country)
        if fallback:
            dest_city_slug = fallback.slug
            dest_airports = list(fallback.airports)
            unresolved.append(
                f"No destination city was given for {dest_country}; assumed "
                f"{fallback.name} ({'/'.join(fallback.airports)}) for hotel search."
            )
        else:
            unresolved.append(f"No airport/city mapping for destination country {dest_country!r}.")

    accessibility_needs = [
        need for need in travel_request.accessibility_needs
        if need.strip()
    ]

    context = HotelTripContext(
        dest_country=dest_country,
        dest_city=dest_city,
        dest_city_slug=dest_city_slug,
        dest_airports=dest_airports,
        arrival_airport=arrival_airport,
        check_in_date=travel_request.departure_date.isoformat(),
        check_out_date=travel_request.return_date.isoformat(),
        traveller_ages=list(travel_request.traveller_ages),
        budget_total=travel_request.budget,
        currency=travel_request.currency,
        accessibility_needs=accessibility_needs,
        preferences=list(travel_request.preferences),
        refinement_notes=list(travel_request.refinement_notes),
        hotel_preferences=derive_hotel_preferences(list(travel_request.preferences)),
    )
    return context, unresolved


def to_hotel_request(
    travel_request,
    *,
    passport_country: str | None = None,
    flight_candidates: list[dict] | None = None,
    constraints: HotelConstraints | None = None,
) -> AdaptedRequest:
    """The full adaptation: shared `TravelRequest` -> `HotelProposalRequest`."""
    arrival_airport = None
    if flight_candidates:
        outbound = next(
            (c for c in flight_candidates if c.get("direction") == "OUTBOUND"),
            None,
        )
        if outbound:
            arrival_airport = outbound.get("dest_airport")

    context, unresolved = to_hotel_trip_context(
        travel_request, arrival_airport=arrival_airport
    )
    return AdaptedRequest(
        request=HotelProposalRequest(
            trip_context=context,
            constraints=constraints,
            flight_candidates=list(flight_candidates or []),
        ),
        unresolved=unresolved,
    )
