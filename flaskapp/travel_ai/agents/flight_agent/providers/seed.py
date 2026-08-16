"""The static dataset, behind the provider interface.

Deliberately trivial: it hands back `SEED_FLIGHT_INVENTORY` whole and lets
`domain.py` do the route and date filtering exactly as it always has. Wrapping
it in a class buys one thing — the seed path and a live supplier path become
the same shape to agent.py — and it must buy that without changing a single
ranking outcome, because the golden scenarios and ~100 tests are pinned to
this dataset's exact output.
"""

from __future__ import annotations

from flaskapp.travel_ai.agents.flight_agent.providers.base import InventoryResult
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY, covers_route

INVENTORY_ASSUMPTION = (
    "Fare, schedule and seat availability are read from the project's static "
    "flight inventory, not a live supplier feed. Verify with the carrier before booking."
)


class SeedInventoryProvider:
    """`SEED_FLIGHT_INVENTORY`, unfiltered — domain.py narrows it."""

    name = "seed"
    assumption = INVENTORY_ASSUMPTION

    def covers(self, request: FlightProposalRequest) -> bool:
        """Whether the dataset stocks any airport pair for this trip.

        Airport resolution alone is not enough: `places.py` maps far more
        cities than the CSV has flights for, so a routable-but-unstocked trip
        would otherwise reach the grounded path and return an empty proposal
        that reads as a bug rather than a coverage gap.

        Asked of the rows themselves rather than a hand-maintained country
        allow-list, so regenerating the CSV updates coverage automatically and
        the two can no longer disagree.
        """
        ctx = request.trip_context
        return covers_route(ctx.origin_airports, ctx.dest_airports)

    def fetch(self, request: FlightProposalRequest) -> InventoryResult:
        return InventoryResult(items=list(SEED_FLIGHT_INVENTORY))
