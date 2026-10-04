"""An empty leg walks nearby dates in code, and an unrecoverable one says what was searched.

Pinned to a real failure, twice. On 2 October a traveller asked for Singapore to
Tokyo, 29 Nov out and 3 Dec back. The route flies neither date. The loop was
told "this route flies on 2026-11-30", spent its six tool calls probing other
dates, and returned nothing after 112 seconds.

PR #55 moved the retry into code, but only for a search that left the date out.
On 4 October the same trip was re-run live: gpt-5 wrote the traveller's own date
into every search, so the retry never fired and the 30 Nov return was never
searched. The tests at the time passed because they left the date out. The
`..._writes_the_travellers_own_date` tests below copy what the live model did.

The walk now behaves as if the source were a real, paid supplier: one date per
search, nearest first (-1, +1, -2, +2, -3, +3, earlier first on a tie), stopping
at the first date with flights. It never reads the answer off the loaded data.

The two legs still fail differently, and each is the test of one half:

* the **return** leg's nearest flying date (30 Nov) is exactly
  `MAX_DATE_SHIFT_DAYS` away, inside the window, so the walk must reach it;
* the **outbound** leg's nearest is four days away, outside the window, so the
  only honest answer is saying which dates were searched.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from flaskapp.travel_ai.agents.flight_agent import tools
from flaskapp.travel_ai.agents.flight_agent.agent import _coverage_warnings, _searched_span
from flaskapp.travel_ai.agents.flight_agent.agentic import run_agentic_flight_agent
from flaskapp.travel_ai.agents.flight_agent.domain import (
    flyable_dates_for_leg,
    propose_flights,
)
from flaskapp.travel_ai.agents.flight_agent.providers.base import InventoryResult
from flaskapp.travel_ai.agents.flight_agent.providers.seed import SeedInventoryProvider
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY
from flaskapp.travel_ai.agents.loop import LoopBudget
from flaskapp.travel_ai.tracing import AuditTracer

from conftest import tool_call

SCENARIOS = json.loads(
    (Path(__file__).parent / "golden" / "flight_scenarios.json").read_text()
)
ROUND_0 = next(s for s in SCENARIOS if s["id"] == "golden_scenario_1_round0")

# The traveller's own dates, and what the seed timetable actually offers.
DEPART = "2026-11-29"
RETURN = "2026-12-03"
RETURN_RECOVERABLE_DATE = "2026-11-30"  # exactly -3 days: the window's edge

# The return walk, in order, until it reaches 30 Nov: -1, +1, -2, +2, -3.
RETURN_WALK = [RETURN, "2026-12-02", "2026-12-04", "2026-12-01", "2026-12-05", "2026-11-30"]
# The outbound walk with the return still on 3 Dec: all six days, none fly.
OUTBOUND_WALK = [
    DEPART, "2026-11-28", "2026-11-30", "2026-11-27", "2026-12-01", "2026-11-26", "2026-12-02",
]


def _request() -> FlightProposalRequest:
    """The 2 October request, as the adapter would have produced it."""
    base = FlightProposalRequest.model_validate(ROUND_0["request"])
    context = base.trip_context.model_copy(update={
        "depart_date": DEPART,
        "return_date": RETURN,
        "party": {"adults": 2, "children": 0},
        "budget_total": 5000,
        # The real request had none; the golden scenario does, and leaving it on
        # would mean a wheelchair filter could mask a date failure.
        "accessibility_needs": [],
    })
    return base.model_copy(update={"trip_context": context})


def _context(tracer=None, provider=None, budget=None) -> tools.ToolContext:
    ctx = tools.ToolContext.for_request(
        _request(), provider or SeedInventoryProvider(), (budget or LoopBudget()).start()
    )
    ctx.tracer = tracer
    return ctx


class DatedSupplier:
    """A stand-in for a real supplier: each fetch searches its own dates only.

    Built over the seed rows, but unlike `SeedInventoryProvider` it is not
    static. It returns only the flights departing on the request's two leg
    dates, and records every fetch, so a test can see the walk asking one date
    at a time rather than reading the answer off the whole dataset.
    """

    name = "dated-supplier"
    is_static = False

    def __init__(self):
        self.fetched: list[tuple[str, str]] = []

    def covers(self, request):
        return True

    def fetch(self, request):
        ctx = request.trip_context
        self.fetched.append((ctx.depart_date, ctx.return_date))
        wanted = {ctx.depart_date, ctx.return_date}
        return InventoryResult(items=[
            item for item in SEED_FLIGHT_INVENTORY if item.dep_ts[:10] in wanted
        ])


# --- The scenario is still the scenario --------------------------------------
#
# Seed inventory is regenerated from time to time. If a timetable change gives
# this route a flight on 29 Nov, every test below would pass for the wrong
# reason, so the shape of the gap is asserted first and separately.


def test_neither_requested_date_has_inventory():
    """The premise. Without this the rest of the file proves nothing."""
    request = _request()
    for direction in ("OUTBOUND", "RETURN"):
        assert flyable_dates_for_leg(
            SEED_FLIGHT_INVENTORY, request, direction, within_days=0
        ) == [], f"{direction} now has inventory on the requested date"


def test_the_two_legs_fail_differently():
    """One leg reachable inside the window, one not — the whole point."""
    request = _request()
    window = tools.MAX_DATE_SHIFT_DAYS

    assert flyable_dates_for_leg(
        SEED_FLIGHT_INVENTORY, request, "RETURN", within_days=window
    ) == [RETURN_RECOVERABLE_DATE]
    assert flyable_dates_for_leg(
        SEED_FLIGHT_INVENTORY, request, "OUTBOUND", within_days=window
    ) == [], "the outbound leg is supposed to be unreachable"


# --- Half one: the leg the walk can recover ---------------------------------


def test_an_empty_return_leg_recovers_when_the_date_is_left_out():
    result = tools.search_flights(_context(), direction="RETURN")

    assert result["included_count"] > 0, "the empty leg did not recover"
    assert result["searched_date"] == RETURN_RECOVERABLE_DATE
    assert result["rows"], "included_count disagrees with rows"


def test_an_empty_return_leg_recovers_when_the_model_writes_the_travellers_own_date():
    """What gpt-5 did on 4 Oct: the traveller's date, written in. Under #55 this
    returned nothing, because only a left-out date triggered the retry."""
    result = tools.search_flights(_context(), direction="RETURN", depart_date=RETURN)

    assert result["included_count"] > 0, "a written-in own date did not trigger the walk"
    assert result["searched_date"] == RETURN_RECOVERABLE_DATE


def test_the_walk_goes_nearest_first_and_earlier_first_on_a_tie():
    result = tools.search_flights(_context(), direction="RETURN")

    assert result["dates_searched"] == RETURN_WALK


def test_the_walk_searches_one_date_at_a_time():
    """As if the source were a paid supplier: one fetch per date, in walk order,
    stopping at the first date with flights. No reading the answer off the data."""
    supplier = DatedSupplier()
    ctx = _context(provider=supplier, budget=LoopBudget(max_provider_calls=20))

    result = tools.search_flights(ctx, direction="RETURN", depart_date=RETURN)

    assert result["searched_date"] == RETURN_RECOVERABLE_DATE
    assert [ret for _out, ret in supplier.fetched] == RETURN_WALK


def test_the_walk_stops_when_the_search_budget_runs_out():
    """Out of paid searches, it stops and claims only the dates it searched."""
    supplier = DatedSupplier()
    ctx = _context(provider=supplier, budget=LoopBudget(max_provider_calls=2))

    result = tools.search_flights(ctx, direction="RETURN")

    assert len(supplier.fetched) == 2
    assert result["included_count"] == 0
    assert ctx.searched_dates["RETURN"] == set(RETURN_WALK[:2])
    assert result["dates_searched"] == RETURN_WALK[:2]


def test_the_recovered_date_is_disclosed_not_substituted():
    """A search that quietly moved a traveller's date would be worse than one
    that returned nothing. The move must be visible three ways."""
    ctx = _context()
    result = tools.search_flights(ctx, direction="RETURN")

    assert result["auto_retried_from_date"] == RETURN, "the result hides the move"
    assert ctx.effective_dates["RETURN"] == RETURN_RECOVERABLE_DATE
    notes = ctx.date_shift_notes()
    assert any(RETURN in note and RETURN_RECOVERABLE_DATE in note for note in notes), (
        f"no traveller-facing disclosure of the shift: {notes}"
    )


def test_the_walk_is_traced_as_the_agents_decision(tmp_path):
    """One event for the whole walk, distinguishable from searches the model
    asked for. Reading a production trace is how both bugs were found."""
    tracer = AuditTracer(tmp_path, "req-empty-leg")
    tools.search_flights(_context(tracer), direction="RETURN")

    entries = [
        json.loads(line) for line in tracer.path.read_text().splitlines() if line.strip()
    ]
    events = [e for e in entries if e["event"] == tools.EVENT_AUTO_DATE_RETRY]
    assert len(events) == 1, f"expected exactly one walk event, got {events}"
    details = events[0]["details"]
    assert details["from_date"] == RETURN
    assert details["to_date"] == RETURN_RECOVERABLE_DATE
    assert details["dates_searched"] == len(RETURN_WALK)
    called = [e for e in entries if e["event"] == "agent_tool_called"]
    assert len(called) == 1, "the walk's own steps were logged as model tool calls"


def test_the_walk_costs_no_extra_tool_call():
    """The tool-call budget is what the loop ran out of. A walk that spent tool
    calls would fix one request by starving the next."""
    budget = LoopBudget().start()
    ctx = tools.ToolContext.for_request(_request(), SeedInventoryProvider(), budget)
    before = budget.as_counts()["tool_calls"]

    tools.search_flights(ctx, direction="RETURN")

    assert budget.as_counts()["tool_calls"] == before


def test_the_walk_never_leaves_the_window():
    result = tools.search_flights(_context(), direction="OUTBOUND")

    base = date.fromisoformat(DEPART)
    for day in result["dates_searched"]:
        shift = abs((date.fromisoformat(day) - base).days)
        assert shift <= tools.MAX_DATE_SHIFT_DAYS, f"walked to {day}, {shift} days away"


def test_a_populated_leg_is_left_alone():
    """The negative control. A leg with flights on the asked-for date must not
    be moved, and must carry no sign of a walk that never happened."""
    base = FlightProposalRequest.model_validate(ROUND_0["request"])
    ctx = tools.ToolContext.for_request(base, SeedInventoryProvider(), LoopBudget().start())

    result = tools.search_flights(ctx, direction="OUTBOUND")

    assert result["included_count"] > 0
    assert result["date_shift_days"] == 0
    assert "auto_retried_from_date" not in result
    assert "dates_searched" not in result
    assert ctx.effective_dates.get("OUTBOUND") == base.trip_context.depart_date


def test_a_different_date_the_model_names_is_answered_as_asked():
    """The guard that stays. Asked about 2 Dec, the answer is about 2 Dec, even
    though 30 Nov would have had flights. Anything else is a silent substitution."""
    result = tools.search_flights(_context(), direction="RETURN", depart_date="2026-12-02")

    assert result["included_count"] == 0
    assert result["searched_date"] == "2026-12-02"
    assert "auto_retried_from_date" not in result
    assert "dates_searched" not in result


# --- Steering: the model may say which way, the code still walks -------------


def test_shift_preference_earlier_walks_earlier_only():
    result = tools.search_flights(_context(), direction="RETURN", shift_preference="earlier")

    assert result["searched_date"] == RETURN_RECOVERABLE_DATE
    assert result["dates_searched"] == [RETURN, "2026-12-02", "2026-12-01", "2026-11-30"]


def test_shift_preference_later_never_searches_earlier():
    """'Can't come back before the 3rd': 30 Nov must not be offered, even though
    it is the only date in the window that flies."""
    result = tools.search_flights(_context(), direction="RETURN", shift_preference="later")

    assert result["included_count"] == 0
    assert result["dates_searched"] == [RETURN, "2026-12-04", "2026-12-05", "2026-12-06"]


def test_an_unknown_shift_preference_is_refused_not_guessed():
    result = tools.search_flights(_context(), direction="RETURN", shift_preference="sooner")

    assert result["error"] == tools.REASON_INVALID_ARGS
    assert result["allowed"]["shift_preference"] == list(tools.SHIFT_PREFERENCES)


def test_shift_preference_is_offered_to_the_model():
    spec = next(s for s in tools.TOOL_SPECS if s["function"]["name"] == "search_flights")
    prop = spec["function"]["parameters"]["properties"]["shift_preference"]
    assert prop["enum"] == list(tools.SHIFT_PREFERENCES)


# --- The trip stays in order --------------------------------------------------


def test_the_outbound_never_moves_past_the_return():
    """With the return already moved to 30 Nov, the outbound walk may not try
    1 or 2 Dec: you cannot fly out after you have flown home."""
    ctx = _context()
    tools.search_flights(ctx, direction="RETURN")          # moves the return to 30 Nov
    result = tools.search_flights(ctx, direction="OUTBOUND")

    returning = date.fromisoformat(RETURN_RECOVERABLE_DATE)
    assert all(date.fromisoformat(d) <= returning for d in result["dates_searched"]), (
        f"walked the outbound past the return: {result['dates_searched']}"
    )
    assert result["dates_searched"] == [
        DEPART, "2026-11-28", "2026-11-30", "2026-11-27", "2026-11-26",
    ]


# --- Half two: what the traveller is told -----------------------------------


def test_an_unreachable_leg_reports_what_was_searched():
    """Nothing inside the window, so the walk finds nothing, and says so."""
    result = tools.search_flights(_context(), direction="OUTBOUND", depart_date=DEPART)

    assert result["included_count"] == 0
    assert result["dates_searched"] == OUTBOUND_WALK
    assert "auto_retried_from_date" not in result
    assert "dates_this_route_flies_nearby" not in result, "still reading answers off the data"


def test_the_warning_names_only_dates_that_were_searched():
    ctx = _context()
    tools.search_flights(ctx, direction="OUTBOUND")
    proposal = ctx.final_proposal(ctx.cache.all_rows)

    warnings = _coverage_warnings(proposal, ctx.resolved_request(), ctx.searched_dates)
    outbound = next(w for w in warnings if "outbound" in w)

    assert outbound == (
        "No outbound flight is available between 2026-11-26 and 2026-12-02. "
        "Try an earlier or later date."
    )


def test_the_warning_never_suggests_an_outbound_after_the_return():
    """The old warning offered 4 and 12 Dec for a trip returning on 3 Dec."""
    ctx = _context()
    tools.search_flights(ctx, direction="OUTBOUND")
    warnings = _coverage_warnings(
        ctx.final_proposal(ctx.cache.all_rows), ctx.resolved_request(), ctx.searched_dates
    )
    outbound = next(w for w in warnings if "outbound" in w)

    assert "flies on" not in outbound
    for token in outbound.replace(".", " ").split():
        if token.startswith("2026-"):
            assert token <= RETURN, f"outbound warning names {token}, after the return"


def test_the_single_shot_path_claims_only_the_date_it_searched():
    """No walk ran, so the warning may only speak for the traveller's own date."""
    request = _request()
    warnings = _coverage_warnings(propose_flights(request, SEED_FLIGHT_INVENTORY), request, None)

    assert warnings == [
        f"No outbound flight is available on {DEPART}. Try an earlier or later date.",
        f"No return flight is available on {RETURN}. Try an earlier or later date.",
    ]


def test_the_span_never_claims_a_day_that_was_not_searched():
    asked = date(2026, 11, 29)
    assert _searched_span(asked, set()) == "on 2026-11-29"
    assert _searched_span(asked, {"2026-11-28", "2026-11-30"}) == (
        "between 2026-11-28 and 2026-11-30"
    )
    assert _searched_span(asked, {"2026-12-02"}) == "on any of 2026-11-29, 2026-12-02"


def test_the_warning_keeps_our_architecture_out_of_the_travellers_answer():
    """'the loaded inventory' told the traveller about our provider wiring and
    nothing about their trip."""
    ctx = _context()
    tools.search_flights(ctx, direction="OUTBOUND")
    warnings = _coverage_warnings(
        ctx.final_proposal(ctx.cache.all_rows), ctx.resolved_request(), ctx.searched_dates
    )

    assert warnings, "scenario is no longer a double-empty leg"
    for warning in warnings:
        lowered = warning.lower()
        for leak in ("inventory", "seed", "provider", "cache", "constraints"):
            assert leak not in lowered, f"{leak!r} leaked into a traveller warning: {warning}"


# --- The two halves together --------------------------------------------------


@pytest.mark.parametrize("own_date_written_in", [False, True], ids=["date-left-out", "gpt-5-style"])
def test_the_trip_that_returned_nothing_now_returns_half_a_trip(own_date_written_in):
    """End to end over the tools: the return leg found, the outbound explained,
    nothing invented — whether or not the model writes its own date in."""
    ctx = _context()
    out_args = {"depart_date": DEPART} if own_date_written_in else {}
    ret_args = {"depart_date": RETURN} if own_date_written_in else {}

    outbound = tools.search_flights(ctx, direction="OUTBOUND", **out_args)
    inbound = tools.search_flights(ctx, direction="RETURN", **ret_args)

    assert outbound["included_count"] == 0
    assert inbound["included_count"] > 0

    proposal = ctx.final_proposal(ctx.cache.all_rows)
    directions = {c.direction for c in proposal.candidates}
    assert directions == {"RETURN"}, f"expected a return-only proposal, got {directions}"

    warnings = _coverage_warnings(proposal, ctx.resolved_request(), ctx.searched_dates)
    assert len(warnings) == 1, f"only the outbound leg is empty: {warnings}"
    assert "outbound" in warnings[0]


def test_the_4_october_run_replayed_through_the_loop(stub_tool_llm):
    """The live run's opening, scripted: both legs searched on the traveller's
    own dates, written in. It must now come back with the 30 Nov return."""
    llm = stub_tool_llm([
        [
            tool_call("search_flights", "c1", direction="OUTBOUND", depart_date=DEPART),
            tool_call("search_flights", "c2", direction="RETURN", depart_date=RETURN),
        ],
        [],
    ])
    ctx = _context()

    proposal, _response = run_agentic_flight_agent(ctx, llm)

    returns = [c for c in proposal.candidates if c.direction == "RETURN"]
    assert returns, "the replayed live run still returns no return flight"
    assert all(c.dep_ts.startswith(RETURN_RECOVERABLE_DATE) for c in returns)
    assert not [c for c in proposal.candidates if c.direction == "OUTBOUND"]
