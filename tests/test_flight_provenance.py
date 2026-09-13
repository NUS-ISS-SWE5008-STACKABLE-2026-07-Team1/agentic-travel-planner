"""The provenance ledger: what counts as a real flight id, and who decides.

Today the grounded path cannot fabricate a flight at all — `_candidate_to_option`
builds every `Option` straight from `proposal.candidates`, so the model never
supplies one. That is a *structural* guarantee, and it is the strongest claim
this codebase makes.

A multi-step agent needs a wider notion of "real" than one proposal can express.
A model that searches twice may legitimately discuss a flight found in the first
search but ranked out of the final list: that id is grounded, because a provider
returned it, yet it is absent from `proposal.candidates`. Checking against the
proposal alone would reject a true statement; checking against nothing would
accept a fabricated one. `InventoryCache.seen_ids` is the set in between.

The load-bearing property is where that set is populated. `rows_for` registers
ids and nothing else does — no tool can widen it. If tools could, the guarantee
would depend on every tool remembering to, and the one that forgot would be the
hole. These tests pin that.
"""

import json
from pathlib import Path

import pytest

from flaskapp.travel_ai.agents.flight_agent import tools
from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights
from flaskapp.travel_ai.agents.flight_agent.guardrails import (
    validate_grounded_explanation, validate_grounded_ids,
)
from flaskapp.travel_ai.agents.flight_agent.providers.seed import SeedInventoryProvider
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY
from flaskapp.travel_ai.agents.loop import LoopBudget

SCENARIOS = json.loads(
    (Path(__file__).parent / "golden" / "flight_scenarios.json").read_text()
)
ROUND_0 = next(s for s in SCENARIOS if s["id"] == "golden_scenario_1_round0")

FABRICATED = "SQ999-20260901"


def _request() -> FlightProposalRequest:
    return FlightProposalRequest.model_validate(ROUND_0["request"])


def _context(budget=None) -> tools.ToolContext:
    return tools.ToolContext.for_request(
        _request(), SeedInventoryProvider(), (budget or LoopBudget()).start()
    )


# --- The gate itself ----------------------------------------------------------


def test_fabricated_id_is_rejected():
    assert validate_grounded_ids([FABRICATED], {"SQ632-20260901"}) == [FABRICATED]


def test_known_id_is_accepted():
    assert validate_grounded_ids(["SQ632-20260901"], {"SQ632-20260901"}) == []


def test_empty_membership_set_rejects_everything():
    """The Path 2 shape: with nothing known to be real, nothing can be grounded.
    Failing open here would make the gate useless exactly where it matters."""
    assert validate_grounded_ids(["SQ632-20260901"], set()) == ["SQ632-20260901"]


def test_nothing_mentioned_is_trivially_grounded():
    assert validate_grounded_ids([], {"SQ632-20260901"}) == []


def test_every_offending_id_is_reported_not_just_the_first():
    """The trace records which ids were ungrounded; reporting one would hide the
    rest and understate a bad response."""
    offending = validate_grounded_ids(
        ["SQ632-20260901", FABRICATED, "XX000-20260101"], {"SQ632-20260901"}
    )
    assert offending == [FABRICATED, "XX000-20260101"]


# --- The single-shot wrapper is unchanged ------------------------------------


def test_explanation_wrapper_still_checks_against_the_proposal():
    """The single-shot path's entry point must behave exactly as before: there
    the proposal IS everything the agent saw, so it is the right set."""
    proposal = propose_flights(_request(), SEED_FLIGHT_INVENTORY)
    real = proposal.candidates[0].flight_id

    assert validate_grounded_explanation([real], proposal) == []
    assert validate_grounded_explanation([FABRICATED], proposal) == [FABRICATED]


def test_wrapper_agrees_with_the_widened_gate():
    """Two entry points, one rule — they must not be able to disagree."""
    proposal = propose_flights(_request(), SEED_FLIGHT_INVENTORY)
    known = {c.flight_id for c in proposal.candidates}
    mentioned = [proposal.candidates[0].flight_id, FABRICATED]

    assert validate_grounded_explanation(mentioned, proposal) == validate_grounded_ids(
        mentioned, known
    )


# --- The ledger ---------------------------------------------------------------


def test_searching_registers_ids():
    ctx = _context()
    assert ctx.cache.seen_ids == frozenset()

    tools.search_flights(ctx, direction="OUTBOUND")

    assert ctx.cache.seen_ids, "a search registered nothing"


def test_seen_ids_only_ever_contains_real_inventory():
    """The set the gate trusts must never contain something a provider did not
    return, or the gate is worse than no gate."""
    ctx = _context()
    tools.search_flights(ctx, direction="OUTBOUND")
    tools.search_flights(ctx, direction="RETURN")

    known = {item.flight_id for item in SEED_FLIGHT_INVENTORY}
    assert ctx.cache.seen_ids <= known


def test_seen_ids_accumulate_across_searches_and_never_shrink():
    """The case the widened gate exists for: a flight found early and ranked out
    later is still grounded."""
    ctx = _context()
    tools.search_flights(ctx, direction="OUTBOUND")
    after_first = ctx.cache.seen_ids

    tools.search_flights(ctx, direction="RETURN")
    after_second = ctx.cache.seen_ids

    assert after_first <= after_second


def test_an_id_ranked_out_of_the_final_list_is_still_grounded():
    """Stated end to end, because this is the whole reason the gate widened: the
    proposal would reject it, the ledger accepts it, and it is genuinely real."""
    ctx = _context()
    result = tools.search_flights(ctx, direction="OUTBOUND")
    assert result["excluded_count"], "scenario excludes nothing; cannot demonstrate"

    ranked = tools.rank_flights(ctx, direction="OUTBOUND", top_n=1)
    shortlisted = {row["flight_id"] for row in ranked["rows"]}
    dropped = [
        item.flight_id for item in SEED_FLIGHT_INVENTORY
        if item.flight_id in ctx.cache.seen_ids and item.flight_id not in shortlisted
    ]
    assert dropped, "nothing was ranked out; cannot demonstrate"

    assert validate_grounded_ids(dropped[:1], ctx.cache.seen_ids) == []
    assert validate_grounded_ids(dropped[:1], shortlisted) == dropped[:1]


def test_tools_cannot_widen_the_ledger():
    """`rows_for` is the sole registration point. A tool that could add ids would
    turn a checked guarantee into a hoped-for one."""
    ctx = _context()
    tools.search_flights(ctx, direction="OUTBOUND")
    before = ctx.cache.seen_ids

    # Every non-searching tool, and a refused search.
    tools.rank_flights(ctx, direction="OUTBOUND", priority=["cost"])
    tools.acknowledge_unmet_preference(ctx, field="avoid_red_eye", reason="more options please")
    tools.search_flights(ctx, direction="OUTBOUND", origin_airports=["JFK"])

    assert ctx.cache.seen_ids == before


def test_refused_search_registers_nothing():
    """An out-of-envelope search must not leave ids behind — otherwise the
    envelope bounds what is searched but not what can be claimed."""
    ctx = _context()
    tools.search_flights(ctx, direction="OUTBOUND", depart_date="2027-01-01")

    assert ctx.cache.seen_ids == frozenset()


def test_exhausted_budget_does_not_invent_provenance():
    """When searching stops, the ledger holds what was actually seen and no
    more."""
    ctx = _context(budget=LoopBudget(max_provider_calls=0))
    tools.search_flights(ctx, direction="OUTBOUND")

    assert ctx.cache.seen_ids == frozenset()
    assert validate_grounded_ids(["SQ632-20260901"], ctx.cache.seen_ids) == ["SQ632-20260901"]
