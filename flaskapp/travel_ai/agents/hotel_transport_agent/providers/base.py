"""The inventory seam: where hotel rows come from.

`domain.py` was written to take `inventory: list[HotelInventoryItem]` as a
plain argument and never to reach for a data source itself. That decision is
what makes this file small — a provider only has to *produce* rows; every
filter, rank and screening rule downstream is unchanged and untested against
the source. Adding a supplier is adding a class here, not editing domain logic.

Two rules every provider follows:

1. **Never raise for a data problem.** A timeout, an empty supplier response
   or an unroutable request all yield an `HotelResult` with no items and a
   note saying why. "No hotels" is an answer the orchestrator can negotiate
   around; an exception is not.
2. **Never invent a field the source does not have.** Absent accessibility
   data is `None`, not `True`. See `HotelInventoryItem`'s docstring for why
   that distinction is enforced rather than left to convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import (
    HotelInventoryItem,
    HotelProposalRequest,
)


@dataclass
class HotelResult:
    """Rows for one request, plus anything the traveller should be told about
    how they were obtained.

    `notes` is the same currency as `AdaptedRequest.unresolved` — human-readable
    strings that agent.py appends to `AgentFinding.warnings`.
    """

    items: list[HotelInventoryItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@runtime_checkable
class HotelInventoryProvider(Protocol):
    """What agent.py needs from a source of hotels.

    `covers` exists so the node can tell "this provider has nothing for this
    city" apart from "this provider tried and found nothing", before spending
    a network call or an LLM call.
    """

    name: str

    def covers(self, request: HotelProposalRequest) -> bool: ...

    def fetch(self, request: HotelProposalRequest) -> HotelResult: ...
