"""Budgets: every limit degrades the answer, none of them raises.

Nothing in this codebase bounded a multi-step agent before — no `recursion_limit`,
no token budget, no step cap anywhere in the graph. `LoopBudget` is that bound,
and it is wired and enforced here in Phase B, while the path is still single-shot.
That ordering is deliberate: the exhaustion branches are live and tested *before*
the loop exists to stress them, rather than being written at the same time as the
thing that first exercises them.

The invariant every test below shares: reaching a limit costs breadth, never the
answer. Raising would discard the searches already made, which is strictly worse
for the traveller than a slightly less-explored result — the same reasoning
`providers/base.py` applies one layer down when it forbids raising for a data
problem.
"""

import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.agents.flight_agent import tools
from flaskapp.travel_ai.agents.flight_agent.agent import ESTIMATE_WARNING, NAME, create_node
from flaskapp.travel_ai.agents.flight_agent.providers.base import InventoryResult
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightAgentResponse, FlightProposalRequest,
)
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY
from flaskapp.travel_ai.agents.loop import BUDGET_NOTE, LoopBudget
from flaskapp.travel_ai.schemas import AgentFinding, TravelRequest
from flaskapp.travel_ai.tracing import AuditTracer

SCENARIOS = json.loads(
    (Path(__file__).parent / "golden" / "flight_scenarios.json").read_text()
)
ROUND_0 = next(s for s in SCENARIOS if s["id"] == "golden_scenario_1_round0")


class SpyProvider:
    name = "seed"

    def __init__(self, is_static: bool = False):
        self.is_static = is_static
        self.fetches = 0

    def covers(self, request) -> bool:
        return True

    def fetch(self, request) -> InventoryResult:
        self.fetches += 1
        return InventoryResult(items=list(SEED_FLIGHT_INVENTORY), notes=[])


class _Structured:
    def __init__(self, payload):
        self._payload = payload

    def invoke(self, messages, config=None):
        return self._payload


class StubLLM:
    """Serves both the grounded and prompt-only schemas."""

    def __init__(self):
        self.calls = 0

    def with_structured_output(self, schema, method=None):
        self.calls += 1
        if schema is FlightAgentResponse:
            return _Structured(FlightAgentResponse(
                rationale="Ranked by cost.", highlighted_flight_ids=[], confidence=0.7,
            ))
        return _Structured(AgentFinding(
            agent=NAME, summary="General route guidance.", options=[],
            warnings=[], confidence=0.4,
        ))


@pytest.fixture
def tracer(tmp_path):
    return AuditTracer(tmp_path, "req-budget")


def _request() -> FlightProposalRequest:
    return FlightProposalRequest.model_validate(ROUND_0["request"])


def _context(budget, provider=None) -> tools.ToolContext:
    return tools.ToolContext.for_request(
        _request(), provider or SpyProvider(), budget.start()
    )


def _events(tracer) -> list[str]:
    return [
        json.loads(line)["event"]
        for line in tracer.path.read_text().splitlines() if line.strip()
    ]


def _state() -> dict:
    travel_request = TravelRequest(
        origin="Singapore", destination="Japan",
        departure_date=date(2026, 9, 1), return_date=date(2026, 9, 5),
        travellers=1, traveller_ages=[34], traveller_genders=["female"],
        traveller_accessibility_needs=[[]], budget=4000, currency="SGD",
        preferences=[], accessibility_needs=[],
    )
    correlation_id = uuid4()
    return {
        "request_id": str(correlation_id),
        "request": travel_request.model_dump(mode="json"),
        "findings": [],
        "messages": [request_message(
            correlation_id=correlation_id, sender="orchestrator_agent", recipient=NAME,
            payload_type="TravelRequest", payload=travel_request,
        )],
    }


# --- Individual limits --------------------------------------------------------


def test_provider_budget_stops_fetching_without_raising():
    provider = SpyProvider()
    ctx = _context(LoopBudget(max_provider_calls=1), provider)

    first = tools.search_flights(ctx, direction="OUTBOUND")
    second = tools.search_flights(ctx, direction="OUTBOUND", depart_date="2026-09-02")

    assert provider.fetches == 1
    assert first["rows"], "the first search should have found flights"
    assert "error" not in second


def test_exhausted_provider_budget_serves_what_was_already_found():
    """'We stopped searching' and 'there are no flights' are different answers,
    and only one of them is about the traveller's trip."""
    provider = SpyProvider()
    ctx = _context(LoopBudget(max_provider_calls=1), provider)
    tools.search_flights(ctx, direction="OUTBOUND")

    rows, notes = ctx.cache.rows_for(_request().model_copy(
        update={"trip_context": _request().trip_context.model_copy(
            update={"depart_date": "2026-09-03"}
        )}
    ))

    assert rows, "everything found so far was discarded"
    assert BUDGET_NOTE in notes, "the traveller was not told the search was cut short"


def test_tool_call_budget_refuses_rather_than_raising():
    ctx = _context(LoopBudget(max_tool_calls=1))

    assert "error" not in tools.dispatch(ctx, "search_flights", {"direction": "OUTBOUND"})
    refused = tools.dispatch(ctx, "search_flights", {"direction": "RETURN"})

    assert refused["error"] == "budget_exhausted"
    assert "detail" in refused, "the model was not told how to proceed"


def test_relaxation_budget_allows_exactly_one():
    """Preserves the pre-loop guarantee — at most one relaxation, ever — now
    enforced by the budget rather than by the shape of the call sequence."""
    budget = LoopBudget(max_relaxations=1)
    assert budget.spend_relaxation() is True
    assert budget.spend_relaxation() is False


def test_llm_turn_budget_is_enforced():
    budget = LoopBudget(max_llm_turns=2)
    assert [budget.spend_llm_turn() for _ in range(3)] == [True, True, False]


def test_deadline_expires_on_wall_clock():
    """Turn count cannot express 'one call took forty seconds', and the flight
    node sits on the critical path of a barrier join."""
    now = [1000.0]
    budget = LoopBudget(deadline_seconds=30, _clock=lambda: now[0]).start()

    assert budget.expired() is False
    now[0] += 31
    assert budget.expired() is True
    assert budget.exhausted_limit() == "deadline_seconds"


def test_unstarted_budget_never_looks_expired():
    """`elapsed_seconds` on an unstarted budget must not read as a huge age."""
    budget = LoopBudget(deadline_seconds=0.0)
    assert budget.expired() is False
    assert budget.elapsed_seconds() == 0.0


def test_exhausted_limit_names_the_spent_cap():
    budget = LoopBudget(max_tool_calls=1).start()
    assert budget.exhausted_limit() is None

    budget.spend_tool_call()
    assert budget.exhausted_limit() == "tool_calls"


def test_fresh_resets_counters_but_keeps_limits():
    """Limits come from config once; spend is per traveller. Sharing spend across
    requests would starve later travellers of searches."""
    spent = LoopBudget(max_tool_calls=4)
    spent.spend_tool_call()
    spent.spend_provider_call()

    fresh = spent.fresh()

    assert fresh.max_tool_calls == 4
    assert (fresh.tool_calls, fresh.provider_calls) == (0, 0)
    assert spent.tool_calls == 1, "fresh() mutated the template"


def test_counts_are_integers_for_the_audit_trail():
    counts = LoopBudget().start().as_counts()
    assert set(counts) == {
        "llm_turns", "tool_calls", "provider_calls", "relaxations", "elapsed_ms",
    }
    assert all(isinstance(value, int) for value in counts.values())


# --- At node level ------------------------------------------------------------


def test_node_degrades_to_the_prompt_only_path_when_it_cannot_search(tracer):
    """A zero search budget is the extreme case, and it must land in the existing
    no-inventory branch rather than anywhere new: labelled estimates, the
    provenance marker intact, and no exception."""
    provider = SpyProvider()
    node = create_node(
        StubLLM(), tracer, provider=provider,
        config={"FLIGHT_AGENT_MAX_PROVIDER_CALLS": 0},
    )

    finding = node(_state())["findings"][0]

    assert provider.fetches == 0
    assert ESTIMATE_WARNING in finding.warnings
    assert finding.options == [], "the unbacked path must not name concrete flights"
    assert "agent_fallback" in _events(tracer)


def test_node_traces_budget_exhaustion_with_counts_only(tracer):
    node = create_node(
        StubLLM(), tracer, provider=SpyProvider(),
        config={"FLIGHT_AGENT_MAX_PROVIDER_CALLS": 0},
    )
    node(_state())

    entries = [json.loads(line) for line in tracer.path.read_text().splitlines() if line.strip()]
    exhausted = [e for e in entries if e["event"] == "agent_budget_exhausted"]

    assert exhausted, "reaching the search budget was not recorded"
    details = exhausted[0]["details"]
    assert details["limit"] == "provider_calls"
    assert all(isinstance(value, int) for key, value in details.items() if key != "limit")


def test_node_budget_does_not_leak_between_requests(tracer):
    """One node instance serves every request; a shared budget would let the
    first traveller spend the second traveller's searches."""
    provider = SpyProvider()
    node = create_node(
        StubLLM(), tracer, provider=provider,
        config={"FLIGHT_AGENT_MAX_PROVIDER_CALLS": 1},
    )

    node(_state())
    node(_state())

    assert provider.fetches == 2, "the second request was denied its own search"


def test_node_still_emits_each_lifecycle_event_once(tracer):
    """New budget events must not be named `agent_started`/`agent_completed` —
    the admin monitor counts those, and `test_flight_node.py` pins them."""
    node = create_node(StubLLM(), tracer, provider=SpyProvider())
    node(_state())

    events = _events(tracer)
    assert events.count("agent_started") == 1
    assert events.count("agent_completed") == 1


def test_default_config_leaves_todays_behaviour_unchanged(tracer):
    """Phase B must be invisible: with no `FLIGHT_AGENT_*` settings, the node
    fetches once and returns grounded options exactly as before."""
    provider = SpyProvider()
    node = create_node(StubLLM(), tracer, provider=provider, config={})

    finding = node(_state())["findings"][0]

    assert provider.fetches == 1
    assert finding.options, "the grounded path stopped returning options"
    assert ESTIMATE_WARNING not in finding.warnings
