"""Flight Agent's LangGraph node — the grounded path.

This node does NOT ask a model to think of flights. `domain.py` searches,
filters and ranks real inventory in code first; the model is then given that
finished proposal and asked only to explain it, with `guardrails` rejecting any
flight ID it invents. Removing the model degrades the answer's prose, not its
correctness — the candidate list is identical either way.

Flow:

    A2A request -> adapter.to_flight_request  (shared contract -> flight contract)
                -> domain.propose_flights     (deterministic, always runs)
                -> reasoning.run_flight_agent (model explains; grounding enforced)
                -> AgentFinding               (back onto the shared contract)

Inventory comes from an `InventoryProvider` (see `providers/`), not from a
module-level dataset. Seed is the default; `FLIGHT_INVENTORY_SOURCE=duffel`
swaps in a live supplier search. The node's logic is identical either way — the
only source-dependent things are the assumption text on each option and the
fact that a live feed can leave accessibility unverified.

Fallback: when the route or dates fall outside the loaded inventory, there is
nothing to ground an answer in. Rather than return an empty finding that reads
as a bug, the node falls back to the prompt-only specialist every other agent
uses, and attaches a warning saying the options are model estimates rather than
inventory-backed. `assess_plan` and the traveller both see that distinction. A
live supplier that errors or returns nothing lands in exactly the same branch.

The node never raises on a planning shortfall. "No flights for this route" is
an answer the orchestrator can act on; an exception is not.
"""

from __future__ import annotations

from flaskapp.config import Config
from flaskapp.database import save_agent_run
from flaskapp.travel_ai.a2a import response_message
from flaskapp.travel_ai.agents.base import make_specialist_node
from flaskapp.travel_ai.agents.flight_agent.adapter import to_flight_request
from flaskapp.travel_ai.agents.flight_agent.domain import screen_flights
from flaskapp.travel_ai.agents.flight_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.agents.flight_agent.providers import get_inventory_provider
from flaskapp.travel_ai.agents.flight_agent.providers.seed import INVENTORY_ASSUMPTION
from flaskapp.travel_ai.agents.flight_agent.reasoning import run_flight_agent
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightCandidate, FlightProposal
from flaskapp.travel_ai.schemas import AgentFinding, Option, TravelRequest
from flaskapp.travel_ai.terminal import log_payload
from flaskapp.travel_ai.usage import TokenUsageCallback

NAME = "flight_agent"

# Re-exported from the seed provider, which owns the wording now that each
# provider states its own. Kept importable from here because that is where
# callers have always found it.
__all__ = ["NAME", "INVENTORY_ASSUMPTION", "ESTIMATE_WARNING", "create_node"]

UNVERIFIED_WHEELCHAIR = (
    "Wheelchair assistance could not be verified for this flight — the supplier "
    "feed does not publish it. Confirm directly with the carrier before booking."
)
UNVERIFIED_STEP_FREE = (
    "Step-free boarding could not be verified for this flight — the supplier "
    "feed does not publish it."
)

ESTIMATE_WARNING = (
    "These flight options are model estimates, not drawn from verified inventory. "
    "Confirm every detail with the carrier."
)


# `run_flight_agent` emits its own agent_started/agent_completed because it is
# also usable standalone (the demo scripts call it directly, with no graph).
# Inside the node those two duplicate the lifecycle events this module already
# records, and the admin monitor reads that stream — so they are dropped here
# while everything genuinely new (relaxation applied/rejected, blocked input,
# failed LLM attempts) passes through untouched.
_DUPLICATE_EVENTS = frozenset({"agent_started", "agent_completed"})


class _InnerTracer:
    """Forwards `run_flight_agent`'s events except the lifecycle duplicates."""

    def __init__(self, tracer):
        self._tracer = tracer

    def record(self, event: str, agent: str, details: dict | None = None) -> None:
        if event not in _DUPLICATE_EVENTS:
            self._tracer.record(event, agent, details)


def _accessibility_limitations(candidate: FlightCandidate) -> list[str]:
    """Three-way, because "we checked and it isn't offered" and "nobody told
    us" are different facts and only one of them is a property of the flight.

    Collapsing `None` into the "not offered" message would libel flights that
    do offer assistance; collapsing it into silence would let an unchecked
    flight read as checked. Both failures land on the traveller who can least
    afford to find out at the gate, so unknown gets its own wording.
    """
    limitations: list[str] = []
    if candidate.wheelchair_assist_available is False:
        limitations.append("Wheelchair assistance is not offered on this flight.")
    elif candidate.wheelchair_assist_available is None:
        limitations.append(UNVERIFIED_WHEELCHAIR)
    if candidate.step_free_boarding is None:
        limitations.append(UNVERIFIED_STEP_FREE)
    return limitations


def _candidate_to_option(candidate: FlightCandidate, currency: str, assumption: str) -> Option:
    """One ranked candidate as a shared-contract `Option`.

    `selection_factors` restates only what the deterministic ranking actually
    used, so the traveller-visible reasons match the code that produced the
    order rather than a model's account of it.
    """
    factors = [
        "Direct" if candidate.stops == 0 else f"{candidate.stops} stop(s)",
        f"Arrives {candidate.arr_ts}",
        f"{candidate.seats} seat(s) available",
    ]
    if candidate.wheelchair_assist_available:
        factors.append("Wheelchair assistance available")
    if candidate.step_free_boarding:
        factors.append("Step-free boarding")
    if candidate.seat_fee_estimate:
        factors.append(f"Estimated seat selection fees: {candidate.seat_fee_estimate:.2f} {currency}")

    return Option(
        name=f"{candidate.flight_id} ({candidate.direction.title()})",
        description=(
            f"{candidate.direction.title()} to {candidate.dest_airport}, departing "
            f"{candidate.dep_ts}, arriving {candidate.arr_ts}."
        ),
        estimated_cost=candidate.price + candidate.seat_fee_estimate,
        currency=currency,
        assumptions=[assumption],
        limitations=_accessibility_limitations(candidate),
        selection_factors=factors,
    )


def _coverage_warnings(proposal: FlightProposal) -> list[str]:
    """Name an empty leg explicitly.

    A proposal with an outbound but no return is a partial answer, and saying
    so is more useful to the orchestrator than a shorter option list it has to
    infer the meaning of.
    """
    return [
        f"No {direction.lower()} flight in the loaded inventory satisfies these "
        "dates and constraints."
        for direction in ("OUTBOUND", "RETURN")
        if not any(c.direction == direction for c in proposal.candidates)
    ]


def _build_finding(
    proposal: FlightProposal, response, currency: str, unresolved: list[str], assumption: str
) -> AgentFinding:
    warnings = list(unresolved) + _coverage_warnings(proposal)
    if response.escalate and response.escalation_reason:
        warnings.append(f"Escalation requested: {response.escalation_reason}")
    if response.relaxation_applied is not None:
        warnings.append(
            f"Relaxed the soft preference '{response.relaxation_applied.field}' to find "
            f"viable options: {response.relaxation_applied.reason}"
        )
    return AgentFinding(
        agent=NAME,
        summary=response.rationale,
        options=[_candidate_to_option(c, currency, assumption) for c in proposal.candidates],
        warnings=warnings,
        confidence=response.confidence,
    )


def create_node(llm, tracer, provider=None, config=None):
    """The graph node. Grounded when inventory covers the trip, prompt-only otherwise.

    `provider` and `config` are injection points for tests and scripts;
    `graph.build_travel_graph` calls this with `(llm, tracer)` like every other
    specialist factory, and the provider is resolved from `Config` once, here,
    rather than per request.
    """
    prompt_only_node = make_specialist_node(NAME, INSTRUCTION, llm, tracer)
    provider_note = None
    if provider is None:
        provider, provider_note = get_inventory_provider(
            vars(Config) if config is None else config
        )
    assumption = getattr(provider, "assumption", INVENTORY_ASSUMPTION)

    def flight_node(state) -> dict:
        incoming = next(
            (m for m in state.get("messages", [])
             if m.message_type == "request" and m.recipient == NAME),
            None,
        )
        if incoming is None:
            raise ValueError(f"Missing A2A request for {NAME}")

        travel_request = TravelRequest.model_validate(state["request"])
        adapted = to_flight_request(travel_request)
        notes = [*adapted.unresolved, *([provider_note] if provider_note else [])]

        # One fetch per request, reused by both the proposal and the screening
        # trace below. A live provider bills and blocks on every call, so the
        # two must never independently ask for the same inventory — and they
        # must see the identical row set, or the explainability trace would
        # describe a different search than the one that produced the options.
        inventory = []
        if provider.covers(adapted.request):
            result = provider.fetch(adapted.request)
            inventory = result.items
            notes.extend(result.notes)

        if not inventory:
            # Nothing to ground an answer in — an unstocked route, an
            # unroutable country, or a live search that failed or came back
            # empty. Use the shared prompt-only path, then mark the result as
            # estimated so it is never mistaken for inventory-backed output.
            tracer.record("agent_fallback", NAME, {
                "reason": "no inventory for route/dates",
                "source": provider.name,
                "unresolved": notes,
            })
            result = prompt_only_node(state)
            for finding in result.get("findings", []):
                finding.warnings = [*finding.warnings, *notes, ESTIMATE_WARNING]
            return result

        tracer.record("agent_started", NAME, {"mode": "grounded", "source": provider.name})
        if tracer.database_path:
            save_agent_run(tracer.database_path, state["request_id"], NAME, "processing")

        usage = TokenUsageCallback()
        try:
            proposal, response = run_flight_agent(
                adapted.request, inventory, llm, tracer=_InnerTracer(tracer)
            )
            screening = screen_flights(adapted.request, inventory)
            finding = _build_finding(
                proposal, response, travel_request.currency, notes, assumption
            )
            log_payload(f"REQUEST {state['request_id']} | {NAME.upper()} RESPONSE", finding)
            tracer.record("agent_completed", NAME, {
                "mode": "grounded",
                "source": provider.name,
                "confidence": finding.confidence,
                "option_count": len(finding.options),
                "warning_count": len(finding.warnings),
                "screened_count": len(screening),
                "excluded_count": sum(1 for s in screening if not s.included),
                **usage.as_dict(),
            })
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path, state["request_id"], NAME, "completed",
                    usage.as_dict(), finding,
                )
            outgoing = response_message(
                request=incoming, sender=NAME, payload_type="AgentFinding", payload=finding
            )
            return {"findings": [finding], "messages": [outgoing]}
        except Exception as exc:
            tracer.record("agent_failed", NAME, {"error_type": type(exc).__name__})
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path, state["request_id"], NAME, "failed",
                    usage.as_dict(), error_type=type(exc).__name__,
                )
            raise

    return flight_node
