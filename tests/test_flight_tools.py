"""The three tools: equivalence with the pre-tool pipeline, and the envelope.

Two jobs here.

**Equivalence.** Phase A of the refactor extracts `domain.py`'s one-shot pipeline
into callable pieces without changing what it computes. `search_flights` at its
defaults must find exactly what `propose_flights` finds, or the golden scenarios
are no longer testing the thing the agent actually runs.

**The envelope.** Once a model chooses the arguments, "search near the
traveller's dates" stops being a prompt and has to become a check. These tests
are that check: a date beyond the cap, an airport that serves a different city,
an unknown tool and an exhausted budget must all come back as refusals the model
can read — never as an exception that kills the run, and never as a silently
different trip.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from flaskapp.travel_ai.agents.flight_agent import tools
from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights
from flaskapp.travel_ai.agents.flight_agent.providers.base import InventoryResult
from flaskapp.travel_ai.agents.flight_agent.providers.seed import SeedInventoryProvider
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY
from flaskapp.travel_ai.agents.loop import LoopBudget
from flaskapp.travel_ai.tracing import AuditTracer

SCENARIOS = json.loads(
    (Path(__file__).parent / "golden" / "flight_scenarios.json").read_text()
)
ROUND_0 = next(s for s in SCENARIOS if s["id"] == "golden_scenario_1_round0")


class SpyProvider:
    """Seed inventory, but counting fetches.

    `is_static` mirrors the real seed provider, which is the property that makes
    repeated searching free.
    """

    name = "seed"
    is_static = True

    def __init__(self, is_static: bool = True):
        self.is_static = is_static
        self.fetches = 0

    def covers(self, request) -> bool:
        return True

    def fetch(self, request) -> InventoryResult:
        self.fetches += 1
        return InventoryResult(items=list(SEED_FLIGHT_INVENTORY), notes=[])


def _request(scenario=ROUND_0) -> FlightProposalRequest:
    return FlightProposalRequest.model_validate(scenario["request"])


def _context(provider=None, budget=None, tracer=None) -> tools.ToolContext:
    ctx = tools.ToolContext.for_request(
        _request(), provider or SeedInventoryProvider(), (budget or LoopBudget()).start()
    )
    ctx.tracer = tracer
    return ctx


@pytest.fixture
def tracer(tmp_path):
    return AuditTracer(tmp_path, "req-tools")


# --- Equivalence --------------------------------------------------------------


def test_search_flights_finds_what_propose_flights_finds():
    """The Phase A gate, at tool level."""
    proposal = propose_flights(_request(), SEED_FLIGHT_INVENTORY)
    expected = [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]

    ctx = _context()
    result = tools.search_flights(ctx, direction="OUTBOUND")

    assert [row["flight_id"] for row in result["rows"]][: len(expected)] == expected


def test_search_flights_returns_only_real_inventory_ids():
    """The grounding invariant, restated at the tool boundary: a tool cannot
    return an id the provider never supplied."""
    known = {item.flight_id for item in SEED_FLIGHT_INVENTORY}
    ctx = _context()

    for direction in ("OUTBOUND", "RETURN"):
        result = tools.search_flights(ctx, direction=direction)
        assert {row["flight_id"] for row in result["rows"]} <= known


def test_rank_flights_default_matches_search_order():
    ctx = _context()
    searched = tools.search_flights(ctx, direction="OUTBOUND")
    ranked = tools.rank_flights(ctx, direction="OUTBOUND", top_n=5)

    assert [r["flight_id"] for r in ranked["rows"]] == [
        r["flight_id"] for r in searched["rows"][: len(ranked["rows"])]
    ]


def test_rank_flights_reports_the_order_it_actually_used():
    """`selection_factors` are only honest because the deterministic key produced
    the order, so a caller must be told the order that was really applied."""
    ctx = _context()
    tools.search_flights(ctx, direction="OUTBOUND")
    ranked = tools.rank_flights(ctx, direction="OUTBOUND", priority=["cost", "prefer_direct"])

    assert ranked["effective_priority"][-1] == "cost"
    assert ranked["priority_adjustments"], "moving cost to last was not disclosed"


def test_rank_flights_discloses_inert_arrival_priority():
    """`arrival_time` does nothing without a soft arrival preference. Silently
    ignoring the request would let a caller believe it reordered something."""
    ctx = _context()
    tools.search_flights(ctx, direction="OUTBOUND")
    ranked = tools.rank_flights(ctx, direction="OUTBOUND", priority=["arrival_time"])

    assert any("arrival_time" in note for note in ranked["priority_adjustments"])


# --- The envelope -------------------------------------------------------------


def test_date_beyond_the_cap_is_refused():
    ctx = _context()
    base = date.fromisoformat(_request().trip_context.depart_date)
    far = (base + timedelta(days=tools.MAX_DATE_SHIFT_DAYS + 1)).isoformat()

    result = tools.search_flights(ctx, direction="OUTBOUND", depart_date=far)

    assert result["error"] == "out_of_envelope"
    assert "allowed" in result, "a refusal must tell the model what it may do instead"


def test_date_within_the_cap_is_allowed():
    ctx = _context()
    base = date.fromisoformat(_request().trip_context.depart_date)
    near = (base + timedelta(days=tools.MAX_DATE_SHIFT_DAYS)).isoformat()

    result = tools.search_flights(ctx, direction="OUTBOUND", depart_date=near)

    assert "error" not in result
    assert result["date_shift_days"] == tools.MAX_DATE_SHIFT_DAYS


def test_date_shift_is_measured_against_the_travellers_own_date():
    """Otherwise three successive +3 shifts walk to +9 and the traveller quietly
    gets a different trip. The envelope must not be relative to the last search."""
    ctx = _context()
    base = date.fromisoformat(_request().trip_context.depart_date)
    step = (base + timedelta(days=tools.MAX_DATE_SHIFT_DAYS)).isoformat()

    assert "error" not in tools.search_flights(ctx, direction="OUTBOUND", depart_date=step)

    walked = (base + timedelta(days=tools.MAX_DATE_SHIFT_DAYS * 2)).isoformat()
    result = tools.search_flights(ctx, direction="OUTBOUND", depart_date=walked)

    assert result["error"] == "out_of_envelope"


def test_airport_outside_the_travellers_cities_is_refused():
    """The traveller chose cities; `resolve_route` chose which airports serve
    them. Searching JFK answers a different question than the one asked."""
    result = tools.search_flights(_context(), direction="OUTBOUND", origin_airports=["JFK"])

    assert result["error"] == "out_of_envelope"


def test_unknown_direction_is_refused():
    result = tools.search_flights(_context(), direction="SIDEWAYS")
    assert result["error"] == "invalid_args"


def test_malformed_date_is_refused_not_raised():
    result = tools.search_flights(_context(), direction="OUTBOUND", depart_date="next tuesday")
    assert result["error"] == "invalid_args"


def test_refusals_are_traced_by_code_not_content(tracer):
    """A free-text reason would put model-supplied strings in the audit trail."""
    ctx = _context(tracer=tracer)
    tools.search_flights(ctx, direction="OUTBOUND", origin_airports=["JFK"])

    entries = [json.loads(line) for line in tracer.path.read_text().splitlines() if line.strip()]
    rejected = [e for e in entries if e["event"] == "agent_tool_rejected"]
    assert rejected and rejected[0]["details"] == {
        "tool": "search_flights", "reason_code": "out_of_envelope",
    }


# --- Caching and budgets ------------------------------------------------------


def test_static_provider_collapses_every_search_to_one_fetch():
    """The property that makes seed re-searching free, asserted rather than
    assumed."""
    provider = SpyProvider(is_static=True)
    ctx = _context(provider=provider)

    tools.search_flights(ctx, direction="OUTBOUND")
    tools.search_flights(ctx, direction="OUTBOUND", depart_date="2026-09-02")
    tools.search_flights(ctx, direction="RETURN")

    assert provider.fetches == 1
    assert ctx.cache.searches == 3


def test_non_static_provider_caches_repeats_but_not_new_searches():
    provider = SpyProvider(is_static=False)
    ctx = _context(provider=provider)

    tools.search_flights(ctx, direction="OUTBOUND")
    tools.search_flights(ctx, direction="OUTBOUND")  # identical: a cache hit
    assert provider.fetches == 1

    tools.search_flights(ctx, direction="OUTBOUND", depart_date="2026-09-02")
    assert provider.fetches == 2


def test_provider_budget_degrades_rather_than_raising():
    """An exhausted search budget must not throw away the searches already made:
    'we stopped looking' and 'there are no flights' are different answers."""
    provider = SpyProvider(is_static=False)
    ctx = _context(provider=provider, budget=LoopBudget(max_provider_calls=1))

    first = tools.search_flights(ctx, direction="OUTBOUND")
    second = tools.search_flights(ctx, direction="OUTBOUND", depart_date="2026-09-02")

    assert provider.fetches == 1
    assert "error" not in second
    assert first["rows"], "the first search should still have found flights"


def test_seen_ids_accumulate_across_searches():
    """The provenance ledger. Populated by the cache, never by the tools."""
    ctx = _context()
    assert ctx.cache.seen_ids == frozenset()

    tools.search_flights(ctx, direction="OUTBOUND")

    known = {item.flight_id for item in SEED_FLIGHT_INVENTORY}
    assert ctx.cache.seen_ids <= known
    assert ctx.cache.seen_ids, "searching registered nothing"


# --- Acknowledgment ---------------------------------------------------------------


def test_acknowledgment_without_a_real_gap_is_ignored(tracer):
    """The model's claim that a gap exists is never trusted."""
    ctx = _context(tracer=tracer)
    tools.search_flights(ctx, direction="OUTBOUND")

    result = tools.acknowledge_unmet_preference(
        ctx, field="avoid_red_eye", reason="I would like more options."
    )

    assert result["applied"] is False
    assert result["reason_code"] == "no_matching_gap"
    assert ctx.acknowledgment_applied is None


def test_acknowledgment_cannot_express_a_hard_constraint():
    """The closed `Literal` on `PreferenceAcknowledgment.field` is the fence: budget,
    accessibility and max_stops are not expressible, not merely rejected later."""
    result = tools.acknowledge_unmet_preference(_context(), field="budget", reason="too expensive")

    assert result["error"] == "invalid_args"
    assert "avoid_red_eye" in result["allowed"]["field"]


def test_arrival_acknowledgment_requires_a_direction():
    result = tools.acknowledge_unmet_preference(
        _context(), field="soft_arrival_preference", reason="no viable options"
    )
    assert result["error"] == "invalid_args"


# --- Dispatch -----------------------------------------------------------------


def test_dispatch_runs_a_tool_by_name():
    ctx = _context()
    result = tools.dispatch(ctx, "search_flights", {"direction": "OUTBOUND"})
    assert "rows" in result


def test_dispatch_refuses_an_unknown_tool():
    result = tools.dispatch(_context(), "book_flight", {})
    assert result["error"] == "invalid_args"
    assert "search_flights" in result["allowed"]["tools"]


def test_dispatch_refuses_unknown_arguments_without_raising():
    result = tools.dispatch(_context(), "search_flights", {"airline": "SQ"})
    assert result["error"] == "invalid_args"


def test_dispatch_enforces_the_tool_call_budget():
    """A runaway model must stop costing calls, and must be told why so it can
    conclude with what it has."""
    ctx = _context(budget=LoopBudget(max_tool_calls=2))

    assert "error" not in tools.dispatch(ctx, "search_flights", {"direction": "OUTBOUND"})
    assert "error" not in tools.dispatch(ctx, "search_flights", {"direction": "RETURN"})
    third = tools.dispatch(ctx, "search_flights", {"direction": "OUTBOUND"})

    assert third["error"] == "budget_exhausted"


def test_budget_counts_are_safe_for_the_audit_trail():
    ctx = _context(budget=LoopBudget(max_tool_calls=5))
    tools.dispatch(ctx, "search_flights", {"direction": "OUTBOUND"})

    counts = ctx.budget.as_counts()
    assert counts["tool_calls"] == 1
    assert all(isinstance(value, int) for value in counts.values())


# --- The single-fetch invariant, at node level --------------------------------


def test_node_fetches_inventory_exactly_once(tracer):
    """`agent.py` has always fetched once and shared the rows between the
    proposal and the screening trace, so the explainability record describes the
    same search that produced the options. A live provider bills per call, so a
    second fetch is a real cost as well as a correctness problem.

    Pinned here BEFORE the loop opens: once the model can ask for more searches,
    a regression would look like ordinary behaviour rather than a bug.
    """
    from uuid import uuid4

    from flaskapp.travel_ai.a2a import request_message
    from flaskapp.travel_ai.agents.flight_agent.agent import NAME, create_node
    from flaskapp.travel_ai.agents.flight_agent.schemas import FlightAgentResponse
    from flaskapp.travel_ai.schemas import TravelRequest

    class _Structured:
        def __init__(self, response):
            self._response = response

        def invoke(self, messages, config=None):
            return self._response

    class _LLM:
        def with_structured_output(self, schema, method=None):
            return _Structured(FlightAgentResponse(
                rationale="Ranked by cost.", highlighted_flight_ids=[], confidence=0.7,
            ))

    travel_request = TravelRequest(
        origin="Singapore", destination="Japan",
        departure_date=date(2026, 9, 1), return_date=date(2026, 9, 5),
        travellers=1, traveller_ages=[34], traveller_genders=["female"],
        traveller_accessibility_needs=[[]], budget=4000, currency="SGD",
        preferences=[], accessibility_needs=[],
    )
    correlation_id = uuid4()
    state = {
        "request_id": str(correlation_id),
        "request": travel_request.model_dump(mode="json"),
        "findings": [],
        "messages": [request_message(
            correlation_id=correlation_id, sender="orchestrator_agent", recipient=NAME,
            payload_type="TravelRequest", payload=travel_request,
        )],
    }

    provider = SpyProvider(is_static=False)
    node = create_node(_LLM(), tracer, provider=provider)
    finding = node(state)["findings"][0]

    assert provider.fetches == 1, "the node fetched inventory more than once"
    assert finding.options, "the grounded path returned nothing to check"


# --- Telling the caller WHY a leg is empty ------------------------------------
#
# These exist because of an observed failure against a real model, not a
# hypothetical one. The first version of the histogram keyed on each reason's
# leading clause, so twelve rows rejected for the same cause produced twelve
# distinct keys with a count of one. The model searched, saw no pattern, tried
# acknowledging a preference instead (correctly rejected, since the preference was not
# the problem) and gave up on a leg that had flights two days away.
#
# Every unit test passed at the time. The signal, not the plumbing, was broken.

EMPTY_LEG_DATE = "2026-09-12"   # SIN-NRT is stocked, but not on this date


def _empty_leg_context():
    request = _request()
    context = request.trip_context.model_copy(update={"depart_date": EMPTY_LEG_DATE})
    return tools.ToolContext.for_request(
        request.model_copy(update={"trip_context": context}),
        SeedInventoryProvider(), LoopBudget().start(),
    )


def test_exclusions_are_grouped_by_cause_not_by_row():
    """One bucket per cause. Twelve rows excluded for `wrong_date` is a signal;
    twelve distinct strings is noise."""
    result = tools.search_flights(_empty_leg_context(), direction="OUTBOUND")
    histogram = result["exclusion_reason_histogram"]

    assert result["included_count"] == 0, "scenario is no longer an empty leg"
    assert histogram.get("wrong_date", 0) > 1, f"date exclusions not grouped: {histogram}"
    assert len(histogram) < result["excluded_count"], "one key per row is not a histogram"


def test_empty_leg_reports_dates_the_route_actually_flies():
    """Derived from real rows, never guessed. This is what makes a second search
    worth issuing rather than a shot in the dark."""
    result = tools.search_flights(_empty_leg_context(), direction="OUTBOUND")

    nearby = result["dates_this_route_flies_nearby"]
    assert nearby, "an empty leg gave the caller nothing to act on"
    assert "2026-09-10" in nearby
    assert "suggestion" in result


def test_suggested_dates_are_inside_the_search_envelope():
    """Suggesting a date the envelope would then refuse would send the caller
    into a guaranteed rejection."""
    ctx = _empty_leg_context()
    result = tools.search_flights(ctx, direction="OUTBOUND")

    for day in result["dates_this_route_flies_nearby"]:
        follow_up = tools.search_flights(ctx, direction="OUTBOUND", depart_date=day)
        assert "error" not in follow_up, f"suggested {day} but the envelope refuses it"


def test_suggested_dates_actually_have_flights():
    """The suggestion must be true: searching one of them must return rows."""
    ctx = _empty_leg_context()
    suggested = tools.search_flights(ctx, direction="OUTBOUND")["dates_this_route_flies_nearby"]

    found = tools.search_flights(ctx, direction="OUTBOUND", depart_date=suggested[0])
    assert found["included_count"] > 0, f"{suggested[0]} was suggested but has no flights"


def test_a_populated_leg_does_not_carry_suggestions():
    """Only spend the tokens when the caller needs them."""
    result = tools.search_flights(_context(), direction="OUTBOUND")

    assert result["included_count"] > 0
    assert "dates_this_route_flies_nearby" not in result
    assert "suggestion" not in result
