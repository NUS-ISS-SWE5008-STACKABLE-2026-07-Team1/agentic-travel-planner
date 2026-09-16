"""v1's only `RiskDataProvider`: the CSV-loaded reference tables, unfiltered
by date — `domain.py` does that filtering, the same way it already filters
`flight_agent`'s and `hotel_transport_agent`'s seed inventory by date.

Deliberately trivial, matching `flight_agent.providers.seed`'s own reasoning:
wrapping the loaded data in a class buys one thing — a future live-retrieval
source becomes the same shape to `agent.py` — and it must buy that without
touching what the three lists actually contain.
"""

from __future__ import annotations

from flaskapp.travel_ai.agents.risk_advisory_agent.providers.base import RiskFetchResult
from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskProposalRequest
from flaskapp.travel_ai.agents.risk_advisory_agent.seed_data import (
    DATED_EVENTS,
    SEASONAL_WINDOWS,
    STANDING_FACTS,
)


class SeedRiskProvider:
    name = "seed"

    def covers(self, request: RiskProposalRequest) -> bool:
        """Whether the seed data has anything for this destination.

        Asked of the rows themselves, not a hand-maintained list of covered
        slugs — editing the CSVs to add a sixth city updates coverage
        automatically and the two can no longer disagree.
        """
        if not request.destination_slug:
            return False
        return any(fact["destination_slug"] == request.destination_slug for fact in STANDING_FACTS)

    def fetch(self, request: RiskProposalRequest) -> RiskFetchResult:
        if not request.destination_slug:
            return RiskFetchResult(notes=["No destination slug resolved for this request."])
        slug = request.destination_slug
        return RiskFetchResult(
            standing_facts=[f for f in STANDING_FACTS if f["destination_slug"] == slug],
            seasonal_windows=[w for w in SEASONAL_WINDOWS if w["destination_slug"] == slug],
            dated_events=[e for e in DATED_EVENTS if e["destination_slug"] == slug],
            trust_level="authored",
        )
