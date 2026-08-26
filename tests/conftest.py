"""Shared fixtures.

The repo's first `conftest.py`. Every test file until now rolled its own stub
model, which was fine while each one needed a single canned response — a
tool-calling loop needs a stub that answers differently on each turn, and that is
too much machinery to copy.

**Deliberately additive.** Nothing here replaces an existing per-file stub. The
Phase A/B gate is that the pre-existing flight tests pass *unchanged*, and a
shared fixture that perturbed one of them would defeat exactly the thing it is
supposed to protect. Migration, if it ever happens, is its own change with its own
review.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from flaskapp.travel_ai.agents.flight_agent.schemas import FlightAgentResponse
from flaskapp.travel_ai.agents.loop import LoopBudget


def tool_call(name: str, call_id: str = "call-1", **args) -> dict:
    """One tool call in the shape `bind_tools` models return."""
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class _BoundStub:
    """Stands in for `llm.bind_tools(...)`.

    Replays a script of turns. Each turn is a list of tool calls, or an empty
    list meaning "I am done, no more tools". Running off the end of the script
    yields a bare message, so a test cannot hang the loop by under-specifying it —
    a runaway model is something you opt into by scripting it, not something you
    trip over.
    """

    def __init__(self, turns: list[list[dict]], repeat_last: bool = False):
        self._turns = turns
        self._repeat_last = repeat_last
        self.calls = 0
        self.seen: list[list] = []

    def invoke(self, messages, config=None):
        self.calls += 1
        self.seen.append(messages)
        index = self.calls - 1
        if index < len(self._turns):
            calls = self._turns[index]
        elif self._repeat_last and self._turns:
            calls = self._turns[-1]
        else:
            calls = []
        return AIMessage(content="" if calls else "done", tool_calls=list(calls))


class _StructuredStub:
    def __init__(self, payload, error: Exception | None = None):
        self._payload = payload
        self._error = error
        self.calls = 0

    def invoke(self, messages, config=None):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._payload


class StubToolLLM:
    """A chat model that can be bound to tools and asked for structured output.

    Both halves are scripted independently, because the loop uses them for
    different jobs: the bound model drives the turns, and the structured call is
    the single terminal turn whose result becomes the traveller-facing answer.
    """

    def __init__(
        self,
        turns: list[list[dict]] | None = None,
        final: FlightAgentResponse | None = None,
        *,
        repeat_last: bool = False,
        structured_error: Exception | None = None,
        bind_error: Exception | None = None,
    ):
        self.bound = _BoundStub(turns or [[]], repeat_last=repeat_last)
        self.structured = _StructuredStub(
            final or FlightAgentResponse(
                rationale="Chose the cheapest viable pair.",
                highlighted_flight_ids=[],
                confidence=0.75,
            ),
            structured_error,
        )
        self._bind_error = bind_error
        self.bound_specs: list | None = None

    def bind_tools(self, specs):
        if self._bind_error is not None:
            raise self._bind_error
        self.bound_specs = specs
        return self.bound

    def with_structured_output(self, schema, method=None):
        return self.structured


@pytest.fixture
def stub_tool_llm():
    """Factory for `StubToolLLM`, so a test reads as its own script.

    A factory rather than an instance: nearly every loop test needs a different
    sequence of turns, and a fixture returning one configured model would be
    re-configured in every test that used it.
    """
    return StubToolLLM


@pytest.fixture
def frozen_budget():
    """A `LoopBudget` whose clock a test controls.

    Returns `(budget, advance)`; call `advance(seconds)` to move time. Wall-clock
    expiry is otherwise untestable without sleeping, and a test suite that sleeps
    is a test suite people stop running.
    """
    def _make(**limits):
        now = [1000.0]
        budget = LoopBudget(_clock=lambda: now[0], **limits)

        def advance(seconds: float) -> None:
            now[0] += seconds

        return budget, advance

    return _make
