"""Flight Agent package.

Two layers live here, and they are not yet connected:

- **The graph node** (`agent.py`, `prompt.INSTRUCTION`) — what the compiled
  LangGraph workflow runs today. Prompt-only: the model is handed the travel
  request and asked for an `AgentFinding`, with no inventory behind it.
- **The deterministic layer** (`domain.py`, `schemas.py`, `guardrails.py`,
  `seed_data.py`, `reasoning.py`, `adapter.py`, `airports.py`) — searches,
  filters and ranks real inventory in code, then uses the model only to explain
  what the code already decided, with grounding enforced by
  `guardrails.validate_grounded_explanation`.

`agent.py` does not call the deterministic layer yet. Connecting them is a
single change to `create_node` — `adapter.to_flight_request` produces the input
`reasoning.run_flight_agent` needs — but it changes behaviour the whole graph
sees, so it is left as its own reviewed step rather than folded into the port.

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
    # Where inventory comes from — seed by default, Duffel when configured
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
