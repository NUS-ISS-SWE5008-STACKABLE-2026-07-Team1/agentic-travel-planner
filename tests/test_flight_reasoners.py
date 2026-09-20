"""The mode gate, tested where it now lives.

`agent.py` used to decide inline which way to think and then branch on the
answer twice more — once to build the screening trace, once to label the trace
event. `reasoners.py` made that one decision returning one object, which is what
makes it testable without running a node, a graph or a model.

What matters here is the decision, not the reasoning itself: `test_flight_agent.py`
covers the single-shot path and `test_flight_agentic_loop.py` the loop. These
tests assert that the right one is chosen, that a model which cannot call tools
is never handed the loop, and that `ReasoningOutcome` carries the four things the
node needs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flaskapp.travel_ai.agents.flight_agent import reasoners
from flaskapp.travel_ai.agents.flight_agent.providers.seed import SeedInventoryProvider
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.tools import ToolContext
from flaskapp.travel_ai.agents.loop import LoopBudget

# The same golden round-0 request the tool tests use, so "a leg has flights"
# means the same thing here as everywhere else in the suite.
SCENARIOS = json.loads(
    (Path(__file__).parent / "golden" / "flight_scenarios.json").read_text()
)
ROUND_0 = next(s for s in SCENARIOS if s["id"] == "golden_scenario_1_round0")
EMPTY_LEG_DATE = "2026-09-12"   # SIN-NRT is stocked, but not on this date


def _request(depart_date: str | None = None) -> FlightProposalRequest:
    request = FlightProposalRequest.model_validate(ROUND_0["request"])
    if depart_date is None:
        return request
    context = request.trip_context.model_copy(update={"depart_date": depart_date})
    return request.model_copy(update={"trip_context": context})


def _context(request) -> ToolContext:
    return ToolContext.for_request(
        request, SeedInventoryProvider(), LoopBudget().start()
    )


def _rows(ctx, request):
    rows, _notes = ctx.cache.rows_for(request)
    return rows


class _ToolCallingModel:
    def bind_tools(self, specs):  # pragma: no cover - never invoked here
        raise AssertionError("selection must not call the model")


class _PlainModel:
    """A chat model with no tool calling — the reason the capability is checked
    rather than the exception caught."""


class _RecordingTracer:
    def __init__(self):
        self.events = []

    def record(self, event, agent, details=None):
        self.events.append((event, agent, details or {}))


def test_structured_mode_never_escalates():
    request = _request()
    ctx = _context(request)

    chosen = reasoners.select_reasoner(
        "structured", ctx, _rows(ctx, request), _ToolCallingModel()
    )

    assert chosen is reasoners.STRUCTURED_REASONER
    assert chosen.is_loop is False
    assert chosen.name == "grounded"


def test_agentic_mode_always_escalates():
    request = _request()
    ctx = _context(request)

    chosen = reasoners.select_reasoner(
        "agentic", ctx, _rows(ctx, request), _ToolCallingModel()
    )

    assert chosen is reasoners.AGENTIC_REASONER
    assert chosen.is_loop is True
    assert chosen.name == "agentic"


def test_auto_stays_single_shot_when_both_legs_have_flights():
    """The common path, and the whole reason `auto` is the default."""
    request = _request()
    ctx = _context(request)
    rows = _rows(ctx, request)

    assert reasoners.has_empty_leg(request, rows) is False
    assert reasoners.select_reasoner("auto", ctx, rows, _ToolCallingModel()) is (
        reasoners.STRUCTURED_REASONER
    )


def test_auto_escalates_when_a_leg_comes_back_empty():
    """An unstocked date on a stocked route: the one case the loop was measured
    to improve (0 options -> 6)."""
    request = _request(EMPTY_LEG_DATE)
    ctx = _context(request)
    rows = _rows(ctx, request)
    if not reasoners.has_empty_leg(request, rows):
        pytest.skip("seed inventory now stocks this date; pick another empty leg")

    assert reasoners.select_reasoner("auto", ctx, rows, _ToolCallingModel()) is (
        reasoners.AGENTIC_REASONER
    )


def test_a_model_without_tool_calling_never_gets_the_loop():
    """Escalating to a loop a model cannot run would turn a disappointing answer
    into a failed request. The trace has to say so, too — silently downgrading
    is how a mode setting stops meaning anything."""
    request = _request()
    ctx = _context(request)
    tracer = _RecordingTracer()

    chosen = reasoners.select_reasoner(
        "agentic", ctx, _rows(ctx, request), _PlainModel(), tracer=tracer
    )

    assert chosen is reasoners.STRUCTURED_REASONER
    assert [event for event, _agent, _d in tracer.events] == ["agent_loop_unavailable"]


def test_selection_without_a_tracer_still_downgrades():
    """`tracer` is optional on every other entry point in this package; a
    missing one must not become an AttributeError on the downgrade path."""
    request = _request()
    ctx = _context(request)

    assert reasoners.select_reasoner("agentic", ctx, _rows(ctx, request), _PlainModel()) is (
        reasoners.STRUCTURED_REASONER
    )


def test_both_reasoners_satisfy_the_protocol():
    """Substitutability is the whole point: if these ever diverge, `agent.py`
    starts needing to know which one it holds."""
    for reasoner in (reasoners.STRUCTURED_REASONER, reasoners.AGENTIC_REASONER):
        assert isinstance(reasoner.name, str) and reasoner.name
        assert isinstance(reasoner.is_loop, bool)
        assert callable(reasoner.run)


def test_the_outcome_is_frozen():
    """The node reads a finished result; nothing should edit one in passing."""
    outcome = reasoners.ReasoningOutcome(
        proposal=None, response=None, screening=[], notes=[],
    )

    with pytest.raises(Exception):
        outcome.proposal = "something else"
