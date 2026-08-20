"""Hotel & Transport Agent package.

Two layers live here, and they are now connected:

- **The graph node** (`agent.py`) — what the compiled LangGraph workflow runs.
   It first tries the grounded path: `adapter.to_hotel_request` produces the
   input, `provider.fetch` loads inventory, `domain.propose_hotels` ranks it,
   and `reasoning.run_hotel_agent` asks the model to explain the result with
   grounding enforced by `guardrails.validate_grounded_explanation`.
- **The prompt-only fallback** — when the route or dates fall outside the
   loaded inventory, there is nothing to ground an answer in. The node falls
   back to the shared prompt-only path and marks the result as estimated.

Exports below are the package's public surface. Import from this package,
not from its modules directly, so internal layout can change without breaking
other agents.
"""

from .agent import NAME, INVENTORY_ASSUMPTION, ESTIMATE_WARNING, create_node
from .adapter import AdaptedRequest, to_hotel_request
from .domain import propose_hotels, propose_transport, screen_hotels
from .providers import HotelInventoryProvider, HotelResult, get_hotel_inventory_provider
from .reasoning import run_hotel_agent
from .schemas import (
    HotelCandidate,
    HotelInventoryItem,
    HotelPreferences,
    HotelProposal,
    HotelProposalRequest,
    HotelTripContext,
    TransportOption,
)
from .seed_data import SEED_HOTEL_INVENTORY, SEED_TRANSPORT_OPTIONS, covers_hotels

__all__ = [
    "NAME",
    "INVENTORY_ASSUMPTION",
    "ESTIMATE_WARNING",
    "create_node",
    "AdaptedRequest",
    "to_hotel_request",
    "propose_hotels",
    "propose_transport",
    "screen_hotels",
    "get_hotel_inventory_provider",
    "HotelInventoryProvider",
    "HotelResult",
    "SEED_HOTEL_INVENTORY",
    "SEED_TRANSPORT_OPTIONS",
    "covers_hotels",
    "run_hotel_agent",
    "HotelCandidate",
    "HotelInventoryItem",
    "HotelPreferences",
    "HotelProposal",
    "HotelProposalRequest",
    "HotelTripContext",
    "TransportOption",
]
