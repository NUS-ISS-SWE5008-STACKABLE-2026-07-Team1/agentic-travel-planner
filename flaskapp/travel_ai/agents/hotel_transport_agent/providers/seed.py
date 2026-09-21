"""The static dataset, behind the provider interface.

Deliberately trivial: it hands back `SEED_HOTEL_INVENTORY` whole and lets
`domain.py` do the city and preference filtering exactly as it always has.
Wrapping it in a class buys one thing — the seed path and a live supplier path
become the same shape to agent.py — and it must buy that without changing a
single ranking outcome, because the tests are pinned to this dataset's exact
output.
"""

from __future__ import annotations

from flaskapp.travel_ai.agents.hotel_transport_agent.providers.base import HotelResult
from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import HotelProposalRequest
from flaskapp.travel_ai.agents.hotel_transport_agent.seed_data import (
    SEED_HOTEL_INVENTORY,
    covers_hotels,
)

INVENTORY_ASSUMPTION = (
    "Hotel availability and rates are read from the project's static "
    "hotel inventory, not a live supplier feed. Verify with the property "
    "before booking."
)


class SeedHotelProvider:
    """`SEED_HOTEL_INVENTORY`, unfiltered — domain.py narrows it."""

    name = "seed"
    assumption = INVENTORY_ASSUMPTION
    is_static = True

    def covers(self, request: HotelProposalRequest) -> bool:
        ctx = request.trip_context
        slugs = [ctx.dest_city_slug] if ctx.dest_city_slug else []
        if ctx.dest_city and not ctx.dest_city_slug:
            from flaskapp.places import find_city
            city = find_city(ctx.dest_country, ctx.dest_city)
            if city:
                slugs.append(city.slug)
        return covers_hotels(slugs)

    def fetch(self, request: HotelProposalRequest) -> HotelResult:
        return HotelResult(items=list(SEED_HOTEL_INVENTORY))
