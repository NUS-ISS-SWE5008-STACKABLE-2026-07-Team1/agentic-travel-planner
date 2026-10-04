"""Flight Agent package.

Two layers live here, and they are connected:

- **The deterministic layer** (`domain.py`, `schemas.py`, `guardrails.py`,
  `seed_data.py`, `reasoning.py`, `adapter.py`, `airports.py`) — searches,
  filters and ranks real inventory in code, then uses the model only to explain
  what the code already decided, with grounding enforced by
  `guardrails.validate_grounded_explanation`.
- **The graph node** (`agent.py`) — what the compiled LangGraph workflow runs.
  It calls the deterministic layer whenever the provider covers the trip, and
  falls back to a prompt-only node (`prompt.PATH2_INSTRUCTION`) only when no
  inventory covers the route. That fallback is screened on the way in and out,
  and cannot return concrete flights — see `docs/flight_agent/design.md` §3.

Exports below are the deterministic layer's public surface. Import from this
package, not from its modules directly, so internal layout can change without
breaking other agents.
"""

from .agent import NAME, create_node
from .adapter import AdaptedRequest, needs_wheelchair, to_flight_request, to_trip_context
from .airports import ResolvedRoute, resolve_airport, resolve_route
from .domain import flight_preference_gaps, propose_flights, screen_flights
from .providers import InventoryProvider, InventoryResult, get_inventory_provider
from .reasoning import run_flight_agent
from .schemas import (
    FlightAgentResponse,
    FlightCandidate,
    FlightInventoryItem,
    FlightPreferences,
    FlightProposal,
    FlightProposalRequest,
    TripContext,
)
from .seed_data import SEED_FLIGHT_INVENTORY

__all__ = [
    "NAME",
    "create_node",
    # Adaptation from the shared TravelRequest contract
    "AdaptedRequest",
    "to_flight_request",
    "to_trip_context",
    "needs_wheelchair",
    "resolve_airport",
    "resolve_route",
    "ResolvedRoute",
    # Deterministic tool
    "propose_flights",
    "screen_flights",
    "flight_preference_gaps",
    # Where inventory comes from — the seed dataset
    "get_inventory_provider",
    "InventoryProvider",
    "InventoryResult",
    "SEED_FLIGHT_INVENTORY",
    # LLM reasoning layer over the tool's output
    "run_flight_agent",
    # Contracts
    "FlightAgentResponse",
    "FlightCandidate",
    "FlightInventoryItem",
    "FlightPreferences",
    "FlightProposal",
    "FlightProposalRequest",
    "TripContext",
]
