"""The reserved seam for swapping Risk & Advisory's data source later.

Mirrors `flight_agent/providers/base.py`'s `InventoryProvider` protocol.
`domain.py` depends only on this interface, never on a concrete
implementation — today there is exactly one (`SeedRiskProvider`, reading the
CSV-loaded content in `seed_data.py`), and a future live-retrieval provider
(mirroring `accessibility_agent/retrieval.py`'s pattern) plugs in behind the
same interface without `domain.py`, `reasoning.py`, or `agent.py` changing at
all. See `docs/risk_advisory_agent/design.md` §6/§7.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskProposalRequest


@dataclass
class RiskFetchResult:
    """Rows from the three reference CSVs, plus provenance.

    `trust_level` is not used by anything yet — `SeedRiskProvider` always
    sets `"authored"`, since this data is written by the team, not fetched
    from a third party. The field exists from day one so that guarantee is
    something `domain.py` can assert, not just assume: the day a
    `"retrieved"`-tagged provider exists, that assertion becomes meaningful
    instead of decorative.
    """

    standing_facts: list[dict[str, Any]] = field(default_factory=list)
    seasonal_windows: list[dict[str, Any]] = field(default_factory=list)
    dated_events: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    trust_level: Literal["authored", "retrieved"] = "authored"

    @property
    def is_empty(self) -> bool:
        return not (self.standing_facts or self.seasonal_windows or self.dated_events)


class RiskDataProvider(Protocol):
    name: str

    def covers(self, request: RiskProposalRequest) -> bool: ...

    def fetch(self, request: RiskProposalRequest) -> RiskFetchResult: ...
