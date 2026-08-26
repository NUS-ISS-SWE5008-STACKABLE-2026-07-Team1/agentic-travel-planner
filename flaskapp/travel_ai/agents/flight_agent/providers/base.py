"""The inventory seam: where flight rows come from.

`domain.py` was written to take `inventory: list[FlightInventoryItem]` as a
plain argument and never to reach for a data source itself. That decision is
what makes this file small — a provider only has to *produce* rows; every
filter, rank and screening rule downstream is unchanged and untested against
the source. Adding a supplier is adding a class here, not editing domain logic.

Two rules every provider follows:

1. **Never raise for a data problem.** A timeout, an empty supplier response
   or an unroutable request all yield an `InventoryResult` with no items and a
   note saying why. "No flights" is an answer the orchestrator can negotiate
   around; an exception is not (see agent.py's module docstring).
2. **Never invent a field the source does not have.** Absent accessibility
   data is `None`, not `True`. See `FlightInventoryItem`'s docstring for why
   that distinction is enforced rather than left to convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightInventoryItem,
    FlightProposalRequest,
)


@dataclass
class InventoryResult:
    """Rows for one request, plus anything the traveller should be told about
    how they were obtained.

    `notes` is the same currency as `AdaptedRequest.unresolved` — human-readable
    strings that agent.py appends to `AgentFinding.warnings`. It is where a
    provider says "the supplier does not publish seat counts" or "the live
    search timed out", so a thin result is explained rather than just thin.
    """

    items: list[FlightInventoryItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@runtime_checkable
class InventoryProvider(Protocol):
    """What agent.py needs from a source of flights.

    `covers` exists so the node can tell "this provider has nothing for this
    route" apart from "this provider tried and found nothing", before spending
    a network call or an LLM call. The seed provider answers it from a static
    country list; a live supplier answers it from routability alone.
    """

    name: str

    # Whether `fetch` ignores the request's dates and airports and returns the
    # provider's whole dataset. True for seed, which hands back all its rows and
    # lets `domain.py` narrow them; False for any live supplier, where each fetch
    # is a distinct billed search.
    #
    # It exists so `agents.loop.InventoryCache` can collapse every cache key to
    # one on a static provider, making repeated searches provably free rather
    # than free-if-you-happen-to-know-how-seed-works. Read via `getattr` with a
    # False default, so a provider predating this attribute is treated as billed
    # — the safe direction to be wrong in.
    is_static: bool = False

    def covers(self, request: FlightProposalRequest) -> bool: ...

    def fetch(self, request: FlightProposalRequest) -> InventoryResult: ...
