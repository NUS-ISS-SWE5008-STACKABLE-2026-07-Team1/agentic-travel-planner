"""The two ways this agent can think, behind one interface.

`reasoning.run_flight_agent` (single-shot) and `agentic.run_agentic_flight_agent`
(tool loop) were already written to return the same pair — `agentic.py`'s
docstring calls that out as deliberate, because it is what lets `agent.py` switch
between them without touching `_build_finding`, the trace events or the tests.
What was missing was the interface saying so, so the node carried the branch
inline and each arm separately worked out its own screening trace, its own notes
and its own label for the trace event.

This module is that interface. `FlightReasoner` is the Strategy;
`select_reasoner` is the factory that resolves `FLIGHT_AGENT_MODE` plus the
per-request escalation test into one of them.

**What each path must produce, and why it is four things and not two.** The
`(proposal, response)` pair was never the whole output. The node also needs:

* the **screening trace**, over the rows that actually produced the options —
  and the loop may have searched several times, so its trace is computed over
  everything the cache saw rather than the first fetch;
* the **notes** a path accumulated — date shifts, ranking changes, provider
  remarks — which only the loop generates, and which are disclosure rather than
  decoration: a search that quietly moved a traveller's date would be worse than
  returning nothing.

Returning those alongside the pair is what lets the node stop knowing which path
ran. `ReasoningOutcome` is that return.

**`auto` is a policy, not a third strategy.** It picks per request, from
`has_empty_leg`, which is pure Python over rows already in memory. That is what
keeps the common path at single-shot latency — see `docs/flight_agent/mode-eval.md`
for the measurement the default rests on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from flaskapp.travel_ai.agents.flight_agent.agentic import run_agentic_flight_agent
from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights, screen_flights
from flaskapp.travel_ai.agents.flight_agent.reasoning import run_flight_agent
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightAgentResponse,
    FlightInventoryItem,
    FlightProposal,
    FlightProposalRequest,
)
from flaskapp.travel_ai.agents.flight_agent.tools import ToolContext

AGENT = "flight_agent"


@dataclass(frozen=True)
class ReasoningOutcome:
    """Everything one reasoning path produced, whichever path it was.

    Frozen because the node reads it and nothing should edit a finished result
    on the way past.
    """

    proposal: FlightProposal
    response: FlightAgentResponse
    # The explainability trace: one row per screened flight, included or not.
    # Computed over the same rows that produced `proposal`, never a different
    # search, or the trace would describe work that did not happen.
    screening: list
    # Anything the traveller must be told about how the answer was reached.
    # Empty on the single-shot path, which searches exactly once and has nothing
    # to disclose.
    notes: list[str] = field(default_factory=list)


class FlightReasoner(Protocol):
    """What `agent.py` needs from a way of thinking about a proposal."""

    # Recorded as `mode` on `agent_completed`. Kept on the strategy so the node
    # never has to re-derive the label from the branch it took.
    name: str
    # Whether this is the escalated path, for `flight_agent_eval_runs`.
    is_loop: bool

    def run(
        self,
        ctx: ToolContext,
        inventory: list[FlightInventoryItem],
        llm,
        *,
        tracer=None,
    ) -> ReasoningOutcome: ...


class StructuredReasoner:
    """Single-shot: search once in code, then ask the model to explain it.

    The path this agent has always run and the one-variable revert
    (`FLIGHT_AGENT_MODE=structured`). Takes `inventory` rather than re-reading
    the cache so the node's single-fetch invariant stays a property of the call
    graph rather than of the cache happening to hit — `test_flight_node_providers.py`
    pins it.
    """

    name = "grounded"
    is_loop = False

    def run(self, ctx, inventory, llm, *, tracer=None) -> ReasoningOutcome:
        proposal, response = run_flight_agent(
            ctx.base_request, inventory, llm, tracer=tracer
        )
        return ReasoningOutcome(
            proposal=proposal,
            response=response,
            screening=screen_flights(ctx.base_request, inventory),
        )


class AgenticReasoner:
    """The tool loop: the model chooses what to search and how to rank.

    `inventory` is ignored — and that is the point of it being in the signature
    anyway. The loop reads from `ctx.cache`, which already holds those rows,
    because it may search again and its screening trace must cover every search
    rather than the first one.
    """

    name = "agentic"
    is_loop = True

    def run(self, ctx, inventory, llm, *, tracer=None) -> ReasoningOutcome:
        proposal, response = run_agentic_flight_agent(ctx, llm, tracer=tracer)
        return ReasoningOutcome(
            proposal=proposal,
            response=response,
            screening=screen_flights(ctx.resolved_request(), ctx.cache.all_rows),
            notes=list(ctx.notes),
        )


# Stateless, so one instance each rather than one per request. Every piece of
# per-request state lives in the `ToolContext` that is passed in, which is also
# what makes it safe for two travellers to be reasoned about at the same time.
STRUCTURED_REASONER = StructuredReasoner()
AGENTIC_REASONER = AgenticReasoner()


def has_empty_leg(
    request: FlightProposalRequest, inventory: list[FlightInventoryItem]
) -> bool:
    """Whether the deterministic search leaves either leg with no candidates.

    The escalation test for `auto` mode, and deliberately the cheap one: pure
    Python over rows already fetched, no model call. An empty leg is precisely
    the case the tool loop improves — it can search a nearby date — and a full
    leg is precisely the case where the loop was measured to add latency for an
    identical result.
    """
    proposal = propose_flights(request, inventory)
    directions = {candidate.direction for candidate in proposal.candidates}
    return directions != {"OUTBOUND", "RETURN"}


def select_reasoner(
    mode: str,
    ctx: ToolContext,
    inventory: list[FlightInventoryItem],
    llm,
    *,
    tracer=None,
) -> FlightReasoner:
    """Resolve the configured mode and this request's shape into one strategy.

    `structured` and `agentic` are fixed choices; `auto` — the default — asks
    `has_empty_leg` and escalates only when the deterministic search came back
    short on a leg.

    **Why `auto` and not the loop everywhere.** `docs/flight_agent/mode-eval.md`
    measured the loop's benefit as concentrated almost entirely in one case: a
    stocked route on an unstocked date went from 0 options to 6. On every other
    covered scenario it produced an identical option count for 2-4 extra
    seconds. Making it the global default would tax every traveller for a
    benefit most of them never see — and because the escalation test is free,
    the common path keeps single-shot latency exactly while the requests that
    would otherwise return nothing are the only ones that pay for the loop.

    The tool-calling check is a capability test rather than a caught exception,
    so the trace can say what happened: not every chat model exposes
    `bind_tools`, and since `auto` is the default, a model without it would
    otherwise turn a disappointing answer into a failed request.
    """
    wants_loop = mode == "agentic" or (
        mode == "auto" and has_empty_leg(ctx.base_request, inventory)
    )
    if wants_loop and not hasattr(llm, "bind_tools"):
        if tracer is not None:
            tracer.record("agent_loop_unavailable", AGENT, {
                "reason": "model_does_not_support_tool_calling",
            })
        wants_loop = False
    return AGENTIC_REASONER if wants_loop else STRUCTURED_REASONER
