"""An empty leg recovers itself, and an unrecoverable one says what to ask for.

Pinned to a real failure. On 2 October a traveller asked for Singapore to Tokyo,
29 Nov out and 3 Dec back. The route flies neither date. The loop was handed
"this route flies on 2026-11-30" in its very first RETURN tool result, spent its
six tool calls probing +/-1 day on both legs instead, exhausted its budget and
returned nothing — 112 seconds for zero options and a warning that named no date
the traveller could act on.

The scenario earns its place as a regression test because the two legs fail
*differently*, and each is the test of one half of the fix:

* the **return** leg's nearest flying date is exactly `MAX_DATE_SHIFT_DAYS` away,
  so it is inside the envelope and the agent must find it without being asked;
* the **outbound** leg's nearest is four days away, outside the envelope, so no
  amount of searching can recover it and the only useful answer is naming the
  dates that do fly.

A fix that only did one of those would pass half of this file.
"""

import json
from pathlib import Path

import pytest

from flaskapp.travel_ai.agents.flight_agent import tools
from flaskapp.travel_ai.agents.flight_agent.agent import (
    COVERAGE_SUGGESTION_MAX_DATES,
    _coverage_warnings,
)
from flaskapp.travel_ai.agents.flight_agent.domain import (
    flyable_dates_for_leg,
    propose_flights,
)
from flaskapp.travel_ai.agents.flight_agent.providers.seed import SeedInventoryProvider
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY
from flaskapp.travel_ai.agents.loop import LoopBudget
from flaskapp.travel_ai.tracing import AuditTracer

SCENARIOS = json.loads(
    (Path(__file__).parent / "golden" / "flight_scenarios.json").read_text()
)
ROUND_0 = next(s for s in SCENARIOS if s["id"] == "golden_scenario_1_round0")

# The traveller's own dates, and what the seed timetable actually offers.
DEPART = "2026-11-29"
RETURN = "2026-12-03"
RETURN_RECOVERABLE_DATE = "2026-11-30"  # exactly -3 days: the envelope's edge
OUTBOUND_NEAREST_DATES = ("2026-11-25", "2026-12-04")  # -4 and +5: out of reach


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


def _context(tracer=None) -> tools.ToolContext:
    ctx = tools.ToolContext.for_request(
        _request(), SeedInventoryProvider(), LoopBudget().start()
    )
    ctx.tracer = tracer
    return ctx


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
    """One leg reachable inside the envelope, one not — the whole point."""
    request = _request()
    envelope = tools.MAX_DATE_SHIFT_DAYS

    assert flyable_dates_for_leg(
        SEED_FLIGHT_INVENTORY, request, "RETURN", within_days=envelope
    ) == [RETURN_RECOVERABLE_DATE]
    assert flyable_dates_for_leg(
        SEED_FLIGHT_INVENTORY, request, "OUTBOUND", within_days=envelope
    ) == [], "the outbound leg is supposed to be unreachable"


# --- Half one: the leg the agent can recover ---------------------------------


def test_an_empty_return_leg_recovers_itself_without_being_asked():
    """The production failure, inverted.

    One call, no model, no second tool call: the same single search that
    returned nothing on 2 October now comes back with flights.
    """
    result = tools.search_flights(_context(), direction="RETURN")

    assert result["included_count"] > 0, "the empty leg did not recover"
    assert result["searched_date"] == RETURN_RECOVERABLE_DATE
    assert result["rows"], "included_count disagrees with rows"


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


def test_the_recovery_is_traced_as_the_agents_decision(tmp_path):
    """Distinguishable in the audit trail from a search the model asked for.
    Reading a production trace is how this bug was found in the first place."""
    tracer = AuditTracer(tmp_path, "req-empty-leg")
    tools.search_flights(_context(tracer), direction="RETURN")

    entries = [
        json.loads(line) for line in tracer.path.read_text().splitlines() if line.strip()
    ]
    events = [e for e in entries if e["event"] == tools.EVENT_AUTO_DATE_RETRY]
    assert len(events) == 1, f"expected exactly one retry event, got {events}"
    details = events[0]["details"]
    assert details["from_date"] == RETURN
    assert details["to_date"] == RETURN_RECOVERABLE_DATE


def test_the_recovery_costs_no_extra_tool_call():
    """The budget is what the loop ran out of. A retry that spent a tool call
    would fix one request by starving the next."""
    budget = LoopBudget().start()
    ctx = tools.ToolContext.for_request(_request(), SeedInventoryProvider(), budget)
    before = budget.as_counts()["tool_calls"]

    tools.search_flights(ctx, direction="RETURN")

    assert budget.as_counts()["tool_calls"] == before


def test_recovery_never_leaves_the_envelope():
    """The retry picks its own date, so nothing upstream validates it. It must
    be inside the window the model itself would have been held to."""
    ctx = _context()
    result = tools.search_flights(ctx, direction="RETURN")

    shift = abs(result["date_shift_days"])
    assert shift <= tools.MAX_DATE_SHIFT_DAYS, (
        f"recovered onto a {shift}-day shift, past the {tools.MAX_DATE_SHIFT_DAYS}-day envelope"
    )


def test_a_populated_leg_is_left_alone():
    """The negative control. A leg with flights on the asked-for date must not
    be moved, and must carry no sign of a retry that never happened."""
    base = FlightProposalRequest.model_validate(ROUND_0["request"])
    ctx = tools.ToolContext.for_request(base, SeedInventoryProvider(), LoopBudget().start())

    result = tools.search_flights(ctx, direction="OUTBOUND")

    assert result["included_count"] > 0
    assert result["date_shift_days"] == 0
    assert "auto_retried_from_date" not in result
    assert ctx.effective_dates.get("OUTBOUND") == base.trip_context.depart_date


# --- Half two: the leg no search can save ------------------------------------


def test_an_unreachable_leg_still_returns_the_suggestion():
    """Nothing inside the envelope, so the retry cannot fire and the caller is
    handed the facts instead. This is the path that must NOT be swallowed."""
    result = tools.search_flights(_context(), direction="OUTBOUND")

    assert result["included_count"] == 0
    assert result["dates_this_route_flies_nearby"] == [], (
        "a date inside the envelope should have been recovered, not suggested"
    )
    assert "auto_retried_from_date" not in result


def test_the_warning_names_dates_the_traveller_can_ask_for():
    """The warning the 2 October traveller got named no date at all. Looking
    wider than the search envelope is the entire point: the outbound leg's real
    options are 4 and 5 days out, and the agent may not move them that far."""
    request = _request()
    proposal = propose_flights(request, SEED_FLIGHT_INVENTORY)
    assert not proposal.candidates, "scenario is no longer a double-empty leg"

    warnings = _coverage_warnings(proposal, request, SEED_FLIGHT_INVENTORY)
    outbound = next(w for w in warnings if "outbound" in w)

    assert DEPART in outbound, "the warning does not name the date they asked for"
    for day in OUTBOUND_NEAREST_DATES:
        assert day in outbound, f"{day} flies but was not offered: {outbound}"


def test_the_warning_keeps_our_architecture_out_of_the_travellers_answer():
    """'the loaded inventory' told the traveller about our provider wiring and
    nothing about their trip."""
    request = _request()
    warnings = _coverage_warnings(
        propose_flights(request, SEED_FLIGHT_INVENTORY), request, SEED_FLIGHT_INVENTORY
    )

    assert warnings, "scenario is no longer a double-empty leg"
    for warning in warnings:
        lowered = warning.lower()
        for leak in ("inventory", "seed", "provider", "cache", "constraints"):
            assert leak not in lowered, f"{leak!r} leaked into a traveller warning: {warning}"


def test_the_warning_does_not_read_as_a_timetable():
    """A fortnight of a daily route is a dozen dates. The nearest few are what
    someone would actually consider."""
    request = _request()
    warnings = _coverage_warnings(
        propose_flights(request, SEED_FLIGHT_INVENTORY), request, SEED_FLIGHT_INVENTORY
    )
    outbound = next(w for w in warnings if "outbound" in w)

    assert outbound.count("2026-") <= COVERAGE_SUGGESTION_MAX_DATES + 1, (
        f"too many dates for one warning: {outbound}"
    )


def test_a_route_with_no_nearby_flights_at_all_says_so_plainly():
    """No dates to offer is not a reason to invent an offer."""
    request = _request()
    context = request.trip_context.model_copy(update={
        "depart_date": "2027-06-01", "return_date": "2027-06-08",
    })
    stranded = request.model_copy(update={"trip_context": context})

    warnings = _coverage_warnings(
        propose_flights(stranded, SEED_FLIGHT_INVENTORY), stranded, SEED_FLIGHT_INVENTORY
    )

    assert len(warnings) == 2
    for warning in warnings:
        assert "flies on" not in warning, f"offered dates it does not have: {warning}"


# --- The two halves together --------------------------------------------------


def test_the_trip_that_returned_nothing_now_returns_half_a_trip():
    """End to end over the tools, which is the shape the traveller sees: the
    return leg found, the outbound named, and nothing silently invented."""
    ctx = _context()

    outbound = tools.search_flights(ctx, direction="OUTBOUND")
    inbound = tools.search_flights(ctx, direction="RETURN")

    assert outbound["included_count"] == 0
    assert inbound["included_count"] > 0

    proposal = ctx.final_proposal(ctx.cache.all_rows)
    directions = {c.direction for c in proposal.candidates}
    assert directions == {"RETURN"}, f"expected a return-only proposal, got {directions}"

    warnings = _coverage_warnings(proposal, ctx.resolved_request(), ctx.cache.all_rows)
    assert len(warnings) == 1, f"only the outbound leg is empty: {warnings}"
    assert "outbound" in warnings[0]
    assert DEPART in warnings[0]
