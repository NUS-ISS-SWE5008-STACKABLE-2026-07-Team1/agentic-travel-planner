"""Translation between the shared `TravelRequest` and Risk & Advisory's own contract.

Mirrors `hotel_transport_agent/adapter.py`: everything that knows about both
the shared schema and this agent's own schema lives here, so a change to
either side is a change to one file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flaskapp.places import find_city
from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskProposalRequest
from flaskapp.travel_ai.schemas import TravelRequest


@dataclass
class AdaptedRequest:
    request: RiskProposalRequest
    unresolved: list[str] = field(default_factory=list)


def to_risk_request(travel_request: TravelRequest) -> AdaptedRequest:
    """Resolve a destination slug when possible; never fail without one.

    A slug lets `domain.py` query the reference data directly. Its absence
    is not an error — an unresolved or unmapped destination is a normal
    outcome (see `providers.seed.SeedRiskProvider.covers`), handled by
    falling back to the prompt-only path exactly like Flight/Hotel do for an
    unstocked route.
    """
    unresolved: list[str] = []
    city = find_city(travel_request.destination, travel_request.destination_city)
    slug = city.slug if city else None
    if slug is None:
        unresolved.append(
            f"No reference-data mapping for destination '{travel_request.destination}'"
            + (f" / '{travel_request.destination_city}'" if travel_request.destination_city else "")
        )
    return AdaptedRequest(
        request=RiskProposalRequest(
            destination_slug=slug,
            destination=travel_request.destination,
            departure_date=travel_request.departure_date,
            return_date=travel_request.return_date,
            preferences=list(travel_request.preferences),
            refinement_notes=list(travel_request.refinement_notes),
        ),
        unresolved=unresolved,
    )
