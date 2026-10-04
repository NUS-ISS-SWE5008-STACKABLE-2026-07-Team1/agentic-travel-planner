"""The three callable pieces of `domain.py`, as tools an agent can invoke.

This is the "agentic in process, grounded in fact" seam. The model decides *what
to search*, *how to rank* and *whether a soft preference went unmet*; it never
decides what a flight says. Every row these tools return came from a provider,
every ordering came from `domain._rank_key`, and every acknowledgment was
re-verified against a real, unanimous gap before it took effect — never against
more flights, since acknowledging one never produces any.

**Langchain-free, deliberately.** `reasoning.py` carries the same property so its
grounding and fallback logic can be tested without langchain installed
(`docs/flight_agent/design.md` §7: "must stay that way"). Tools are plain
functions and `TOOL_SPECS` is hand-written OpenAI-format dicts rather than
`@langchain_core.tools.tool` decorators — `bind_tools` accepts those dicts
directly, so the langchain edge stays in `agentic.py` alone.

**The argument envelope is the actual fence.** A prompt asking the model to stay
near the traveller's dates is a request; `_validate_search_args` is a guarantee.
Three rules, all enforced here:

- a searched date must be within `MAX_DATE_SHIFT_DAYS` of the **base** request's
  leg date — measured against the base, never the current one, or three
  successive ±3 shifts walk to ±9;
- searched airports must be a subset of what `airports.resolve_route` already
  resolved for the traveller's own cities, so the model may prefer HND over NRT
  for Tokyo but can never search JFK;
- direction is a closed set.

A violating call returns a refusal *to the model* rather than raising. The model
gets a chance to correct itself, the loop survives, and the traveller's trip is
never quietly changed into a different trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from collections.abc import Callable
from typing import Any

from flaskapp.travel_ai.agents.flight_agent.domain import (
    DEFAULT_RANK_PRIORITY,
    RANK_COMPONENTS,
    acknowledgment_is_valid,
    apply_acknowledgment,
    flight_preference_gaps,
    normalise_priority,
    rank_leg,
    screen_leg,
)
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightProposal,
    FlightProposalRequest,
    PreferenceAcknowledgment,
)
from flaskapp.travel_ai.agents.loop import (
    BUDGET_NOTE,
    EVENT_TOOL_CALLED,
    EVENT_TOOL_REJECTED,
    REASON_BUDGET_EXHAUSTED,
    REASON_INVALID_ARGS,
    REASON_OUT_OF_ENVELOPE,
    InventoryCache,
    LoopBudget,
)

AGENT = "flight_agent"

DIRECTIONS = ("OUTBOUND", "RETURN")

# How far from the traveller's own dates a search may wander. Three days is
# enough to reach the next stocked date on most seed routes (SIN-NRT has rows on
# five dates inside a 42-day window) without turning "Tuesday" into "next week".
MAX_DATE_SHIFT_DAYS = 3

# The code walked nearby dates for an empty leg. Its own event rather than more
# `agent_tool_called` entries, so a trace can tell a search the model asked for
# from the ones the code made on its behalf.
EVENT_AUTO_DATE_RETRY = "agent_auto_date_retry"

# Which way the empty-leg walk may move a date. The model sets it only when the
# traveller gave a reason ("I must be back by the 5th" -> earlier); the code
# still does the walking. "either" is the default and means nearest first.
SHIFT_PREFERENCES = ("either", "earlier", "later")

# Ranked results returned to the model per search. Enough to choose between,
# small enough that several searches do not crowd out the conversation.
MAX_ROWS_RETURNED = 12
MAX_TOP_N = 5


def search_key(request: FlightProposalRequest) -> tuple:
    """Cache identity for a search: the parameters a provider would act on.

    Sorted so that airport order cannot produce two keys for one search.
    """
    ctx = request.trip_context
    return (
        tuple(sorted(ctx.origin_airports)),
        tuple(sorted(ctx.dest_airports)),
        ctx.depart_date,
        ctx.return_date,
    )


def build_cache(provider, budget: LoopBudget, *, on_budget_exhausted=None) -> InventoryCache:
    """The flight-shaped `InventoryCache`, in one place so the node, the tools
    and the tests cannot disagree about the key or the identity function."""
    return InventoryCache(
        provider, budget, key_for=search_key, id_for=lambda item: item.flight_id,
        on_budget_exhausted=on_budget_exhausted,
    )


def budget_from_config(config: dict | None = None) -> tuple[LoopBudget, int]:
    """A `LoopBudget` and date-shift width from `FLIGHT_AGENT_*` settings.

    Takes a plain mapping rather than the `Config` class, matching
    `providers.get_inventory_provider`, so tests can pass a dict and nothing here
    imports Flask config directly.

    Missing keys fall back to the dataclass defaults, so a deployment that
    predates these settings behaves exactly as before.
    """
    values = config or {}
    defaults = LoopBudget()
    budget = LoopBudget(
        max_llm_turns=int(values.get("FLIGHT_AGENT_MAX_LLM_TURNS", defaults.max_llm_turns)),
        max_tool_calls=int(values.get("FLIGHT_AGENT_MAX_TOOL_CALLS", defaults.max_tool_calls)),
        max_provider_calls=int(
            values.get("FLIGHT_AGENT_MAX_PROVIDER_CALLS", defaults.max_provider_calls)
        ),
        deadline_seconds=float(
            values.get("FLIGHT_AGENT_LOOP_DEADLINE_SECONDS", defaults.deadline_seconds)
        ),
    )
    shift = int(values.get("FLIGHT_AGENT_MAX_DATE_SHIFT_DAYS", MAX_DATE_SHIFT_DAYS))
    return budget, shift


@dataclass
class ToolContext:
    """Mutable working state for one agent run.

    `base_request` is the traveller's actual request and is never mutated: it is
    the reference the date envelope is measured against, so an acknowledged
    preference cannot move the goalposts it is checked by. `current_request`
    carries any acknowledgment applied so far.
    """

    base_request: FlightProposalRequest
    current_request: FlightProposalRequest
    cache: InventoryCache
    budget: LoopBudget
    tracer: Any | None = None
    acknowledgment_applied: PreferenceAcknowledgment | None = None
    notes: list[str] = field(default_factory=list)
    # Per-run rather than a module constant so `FLIGHT_AGENT_MAX_DATE_SHIFT_DAYS`
    # can tune it without the envelope becoming a global.
    max_date_shift_days: int = MAX_DATE_SHIFT_DAYS
    # Direction -> the date a search actually found flights on. Empty until a
    # widened search succeeds. Without this the loop can find flights on a nearby
    # date and then throw them away, because the final `propose_flights` filters
    # on exact date equality against the traveller's original request.
    effective_dates: dict[str, str] = field(default_factory=dict)
    # Direction -> the ranking order the caller asked for, when it asked for one
    # at all. Same reason as `effective_dates`: a tool whose result does not
    # survive into the final proposal is a tool that does nothing. Empty means
    # nobody expressed a preference, and the deterministic default applies.
    effective_priority: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Direction -> every date a search actually ran for. The traveller-facing
    # warning may only claim dates that are in here: "no flight between 26 Nov
    # and 2 Dec" must mean those days were searched, not that we peeked.
    searched_dates: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def for_request(
        cls,
        request: FlightProposalRequest,
        provider,
        budget: LoopBudget,
        *,
        max_date_shift_days: int = MAX_DATE_SHIFT_DAYS,
        on_budget_exhausted=None,
    ) -> "ToolContext":
        return cls(
            base_request=request,
            current_request=request,
            cache=build_cache(provider, budget, on_budget_exhausted=on_budget_exhausted),
            budget=budget,
            max_date_shift_days=max_date_shift_days,
        )

    def resolved_request(self) -> FlightProposalRequest:
        """`current_request` with the leg dates that actually produced flights.

        A leg that never found anything keeps the traveller's own date, so the
        "no flight satisfies these dates" warning stays true rather than being
        quietly rewritten to a date they never asked about.

        This is the only place a search's date shift becomes part of the answer,
        and it is paired with `date_shift_notes()` — moving a traveller's date
        without telling them would be worse than returning nothing.
        """
        if not self.effective_dates:
            return self.current_request
        updates = {}
        if "OUTBOUND" in self.effective_dates:
            updates["depart_date"] = self.effective_dates["OUTBOUND"]
        if "RETURN" in self.effective_dates:
            updates["return_date"] = self.effective_dates["RETURN"]
        context = self.current_request.trip_context.model_copy(update=updates)
        return self.current_request.model_copy(update={"trip_context": context})

    def final_proposal(self, rows, *, top_n: int = 3) -> FlightProposal:
        """The traveller-facing proposal: resolved dates, chosen ranking, real rows.

        Built here rather than by `propose_flights` because that function ranks
        both legs with one order and takes no priority, so a caller's chosen
        ordering could never reach the answer — `rank_flights` was, until this
        existed, a tool that cost a turn and changed nothing.

        Still entirely deterministic: `rank_leg` does the sorting, the caller only
        chooses which named component leads, and `normalise_priority` guarantees
        the result is a total order over every component. With no choice
        expressed this is exactly `propose_flights`, which is what keeps the
        golden scenarios valid.
        """
        request = self.resolved_request()
        candidates = []
        for direction in DIRECTIONS:
            origin, dest = _leg_airports(request, direction)
            candidates.extend(rank_leg(
                rows, request, origin=origin, dest=dest,
                leg_date=_leg_date(request, direction), direction=direction,
                top_n=top_n, priority=self.effective_priority.get(direction),
            ))
        return FlightProposal(candidates=candidates)

    def ranking_notes(self) -> list[str]:
        """Disclosure for any leg not shown in the default order."""
        notes = []
        for direction, priority in sorted(self.effective_priority.items()):
            leading = priority[0] if priority else None
            if leading and leading != DEFAULT_RANK_PRIORITY[0]:
                notes.append(
                    f"{direction.title()} options are ordered by "
                    f"{leading.replace('_', ' ')} first rather than by overall cost."
                )
        return notes

    def date_shift_notes(self) -> list[str]:
        """Traveller-facing disclosure for every leg shown on a different date."""
        notes = []
        for direction, used in sorted(self.effective_dates.items()):
            asked = _leg_date(self.base_request, direction).isoformat()
            if used == asked:
                continue
            notes.append(
                f"No {direction.lower()} flight was available on {asked}. "
                f"The options shown depart on {used} instead — confirm this date "
                "works before booking."
            )
        return notes

    def record(self, event: str, details: dict) -> None:
        if self.tracer is not None:
            self.tracer.record(event, AGENT, details)


def _leg_date(request: FlightProposalRequest, direction: str) -> date:
    ctx = request.trip_context
    return date.fromisoformat(ctx.depart_date if direction == "OUTBOUND" else ctx.return_date)


def _leg_airports(request: FlightProposalRequest, direction: str) -> tuple[list[str], list[str]]:
    ctx = request.trip_context
    if direction == "OUTBOUND":
        return list(ctx.origin_airports), list(ctx.dest_airports)
    return list(ctx.dest_airports), list(ctx.origin_airports)


def _refusal(reason_code: str, detail: str, **extra) -> dict:
    """A refusal the model can read and act on, not an exception.

    `detail` is for the model; only `reason_code` reaches the trace, because the
    audit trail holds counts and codes, never free text.
    """
    return {"error": reason_code, "detail": detail, **extra}


def _validate_search_args(
    ctx: ToolContext, direction: str, depart_date: str | None,
    origin_airports: list[str] | None, dest_airports: list[str] | None,
) -> dict | None:
    """The envelope. Returns a refusal dict, or None when the call is allowed."""
    if direction not in DIRECTIONS:
        return _refusal(
            REASON_INVALID_ARGS,
            f"direction must be one of {list(DIRECTIONS)}.",
            allowed={"direction": list(DIRECTIONS)},
        )

    base_date = _leg_date(ctx.base_request, direction)
    if depart_date is not None:
        try:
            requested = date.fromisoformat(depart_date)
        except (TypeError, ValueError):
            return _refusal(
                REASON_INVALID_ARGS, "depart_date must be an ISO date (YYYY-MM-DD).",
            )
        shift = abs((requested - base_date).days)
        if shift > ctx.max_date_shift_days:
            # Measured against the traveller's own date, so repeated shifts
            # cannot accumulate into a different trip.
            return _refusal(
                REASON_OUT_OF_ENVELOPE,
                f"{depart_date} is {shift} days from the traveller's {direction.lower()} "
                f"date; searches may move it by at most {ctx.max_date_shift_days} days.",
                allowed={
                    "earliest": (base_date - timedelta(days=ctx.max_date_shift_days)).isoformat(),
                    "latest": (base_date + timedelta(days=ctx.max_date_shift_days)).isoformat(),
                },
            )

    allowed_origin, allowed_dest = _leg_airports(ctx.base_request, direction)
    for label, requested_codes, permitted in (
        ("origin_airports", origin_airports, allowed_origin),
        ("dest_airports", dest_airports, allowed_dest),
    ):
        if requested_codes is None:
            continue
        if not requested_codes:
            return _refusal(REASON_INVALID_ARGS, f"{label} cannot be empty.")
        unknown = [code for code in requested_codes if code not in permitted]
        if unknown:
            # The traveller chose cities; `airports.resolve_route` chose which
            # airports serve them. Searching outside that set would answer a
            # different question than the one asked.
            return _refusal(
                REASON_OUT_OF_ENVELOPE,
                f"{label} {unknown} do not serve the traveller's chosen cities.",
                allowed={label: permitted},
            )
    return None


def _search_request(
    ctx: ToolContext, direction: str, depart_date: str | None,
    origin_airports: list[str] | None, dest_airports: list[str] | None,
) -> FlightProposalRequest:
    """`current_request` with this search's overrides applied.

    Built from `current_request` so an applied acknowledgment stays in force,
    and only ever narrowed by values the envelope already approved.
    """
    updates: dict[str, Any] = {}
    if depart_date is not None:
        updates["depart_date" if direction == "OUTBOUND" else "return_date"] = depart_date
    if origin_airports is not None or dest_airports is not None:
        current_origin, current_dest = _leg_airports(ctx.current_request, direction)
        legs = (origin_airports or current_origin, dest_airports or current_dest)
        if direction == "OUTBOUND":
            updates["origin_airports"], updates["dest_airports"] = legs
        else:
            updates["dest_airports"], updates["origin_airports"] = legs
    if not updates:
        return ctx.current_request
    context = ctx.current_request.trip_context.model_copy(update=updates)
    return ctx.current_request.model_copy(update={"trip_context": context})


# Exclusion reasons, grouped into the handful of things a caller can actually do
# something about. Matched on the stable wording `domain._screen_item` produces.
_REASON_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wrong_date", ("departs ",)),
    ("not_enough_seats", ("seat(s) available, party needs",)),
    ("over_budget", ("exceeds", "budget")),
    ("arrives_too_late", ("arrives", "after")),
    ("too_many_stops", ("stop",)),
    ("seat_requirement", ("seat", "adjacent", "exit row", "accessible")),
    ("accessibility", ("wheelchair", "step-free")),
)


def _categorise(reason: str) -> str:
    lowered = reason.lower()
    for label, needles in _REASON_CATEGORIES:
        if all(needle.lower() in lowered for needle in needles):
            return label
    return "other"


def _reason_histogram(screening) -> dict[str, int]:
    """Exclusion reasons grouped by CATEGORY and counted.

    Categories, not raw strings. An earlier version keyed on the reason's leading
    clause, which for the commonest case ("departs 2026-09-10, requested
    2026-09-12") produced one bucket per row: twelve keys with a count of one
    each. Against a real model that was actively misleading — it searched, saw no
    pattern in the noise, tried acknowledging a preference as unmet instead
    (correctly rejected, since the preference was not the problem), and gave up
    on a leg that had flights two days away.

    Twelve rows excluded for `wrong_date` is a signal. Twelve distinct strings is
    not.
    """
    histogram: dict[str, int] = {}
    for result in screening:
        for reason in result.reasons:
            key = _categorise(reason)
            histogram[key] = histogram.get(key, 0) + 1
    return histogram


def _walk_offsets(max_days: int, preference: str) -> list[int]:
    """Day offsets the empty-leg walk tries, in order.

    Nearest first, and the earlier day first when two are equally near
    (-1, +1, -2, +2, ...). `preference` drops one side entirely; it never
    reorders the side that is left.
    """
    offsets: list[int] = []
    for distance in range(1, max_days + 1):
        if preference in ("either", "earlier"):
            offsets.append(-distance)
        if preference in ("either", "later"):
            offsets.append(distance)
    return offsets


def _keeps_trip_in_order(ctx: "ToolContext", direction: str, day: date) -> bool:
    """Whether moving this leg to `day` still leaves a trip that makes sense.

    An outbound may not depart after the return, and a return may not depart
    before the outbound. The other leg's date is the one it actually found
    flights on, if it moved, and the traveller's own otherwise. Same-day is
    allowed: a day trip is a real trip.
    """
    other = "RETURN" if direction == "OUTBOUND" else "OUTBOUND"
    other_day = date.fromisoformat(
        ctx.effective_dates.get(other) or _leg_date(ctx.base_request, other).isoformat()
    )
    return day <= other_day if direction == "OUTBOUND" else day >= other_day


# --- The callable half of the registry ------------------------------------
#
# `TOOL_SPECS` below is what the model is shown; this is what `dispatch` runs.
# The two used to be written out separately, so a name could be spelled one way
# in the schema and another in the dict — a drift that costs nothing at import
# and surfaces as "unknown tool" mid-loop, which the model then spends turns
# trying to correct. Registering each tool under its own `__name__` removes one
# of the two hand-written name lists; `tests/test_flight_tools.py` asserts the
# remaining one still agrees with it, in both directions.
_TOOLS: dict[str, Callable[..., dict]] = {}


def _tool(func):
    """Expose a function to the model under its own name."""
    _TOOLS[func.__name__] = func
    return func


def _search_leg_once(
    ctx: ToolContext,
    direction: str,
    depart_date: str | None,
    origin_airports: list[str] | None,
    dest_airports: list[str] | None,
    *,
    record: bool = True,
) -> tuple[dict, list]:
    """One search of one leg: fetch, screen, rank, record. Returns (result, candidates).

    Split out of `search_flights` so the empty-leg walk below runs the same
    search it stands in for rather than a second implementation of it. A walk
    that screened or ranked differently from a normal search would make the
    trace describe work that did not happen.

    `record=False` is for the walk's own steps: they are reported once, as one
    `EVENT_AUTO_DATE_RETRY`, rather than as tool calls the model never made.
    """
    request = _search_request(ctx, direction, depart_date, origin_airports, dest_airports)
    rows, notes = ctx.cache.rows_for(request)
    ctx.notes.extend(note for note in notes if note not in ctx.notes)

    origin, dest = _leg_airports(request, direction)
    leg_date = _leg_date(request, direction)
    # Only a search that actually ran counts as searched. Out of budget, the
    # cache hands back rows it already had, which says nothing about this date.
    if BUDGET_NOTE not in notes:
        ctx.searched_dates.setdefault(direction, set()).add(leg_date.isoformat())
    screening = screen_leg(
        rows, request, origin=origin, dest=dest, leg_date=leg_date, direction=direction
    )
    candidates = rank_leg(
        rows, request, origin=origin, dest=dest, leg_date=leg_date,
        direction=direction, top_n=MAX_ROWS_RETURNED,
    )
    shift = (leg_date - _leg_date(ctx.base_request, direction)).days

    # Only a search that found something moves the leg. A widened search that
    # also came back empty leaves the traveller's own date in place, so the
    # coverage warning still names the date they asked for.
    if candidates:
        ctx.effective_dates[direction] = leg_date.isoformat()

    if record:
        ctx.record(EVENT_TOOL_CALLED, {
            "tool": "search_flights",
            "direction": direction,
            "date_shift_days": shift,
            "included_count": len(candidates),
            "excluded_count": sum(1 for s in screening if not s.included),
        })
    result = {
        "direction": direction,
        "searched_date": leg_date.isoformat(),
        "date_shift_days": shift,
        "rows": [candidate.model_dump(mode="json") for candidate in candidates],
        "included_count": len(candidates),
        "excluded_count": sum(1 for s in screening if not s.included),
        "exclusion_reason_histogram": _reason_histogram(screening),
        "notes": notes,
    }
    return result, candidates


def _is_travellers_own_date(ctx: ToolContext, direction: str, depart_date: str | None) -> bool:
    """Whether this search is for the date the traveller asked for.

    Left out, or written in as the same date: both mean the traveller's date.
    Checking only "left out" was the bug this replaced. A live model (gpt-5, 4
    Oct) wrote the traveller's own date into every search, so the walk below
    never ran, and a return flight three days away was never searched.
    """
    if depart_date is None:
        return True
    return date.fromisoformat(depart_date) == _leg_date(ctx.base_request, direction)


@_tool
def search_flights(
    ctx: ToolContext,
    *,
    direction: str,
    depart_date: str | None = None,
    origin_airports: list[str] | None = None,
    dest_airports: list[str] | None = None,
    shift_preference: str = "either",
) -> dict:
    """Search real inventory for one leg, optionally shifting date or airports.

    If the traveller's own date has no flights, the code walks nearby dates
    itself, one real search at a time: -1, +1, -2, +2, -3, +3 days, earlier
    first on a tie, stopping at the first date with flights. `shift_preference`
    ("earlier" / "later") lets the model keep the walk to one side when the
    traveller gave a reason; the code still does the walking.

    **Why the walk searches date by date instead of looking the answer up.** On
    the seed data every row is already loaded, so the nearest flying date could
    simply be read off. A real supplier cannot be read like that: each date is a
    separate, billed search. The walk behaves the same way on both, so what is
    tested here is what a live supplier would do.

    **Why this is not left to the model.** On 2 Oct the model was told "this
    route flies on 2026-11-30", spent its tool calls probing other dates, and
    returned nothing. Which date to try next is a mechanical decision, so code
    makes it, the same way `domain.py` decides which flights.

    The walk costs no model turn and no tool call. Each date is a real search
    through the cache, which the provider budget still governs. A date the
    model names that is NOT the traveller's own is answered as asked: replying
    about a different date would be a silent substitution.
    """
    if shift_preference not in SHIFT_PREFERENCES:
        ctx.record(EVENT_TOOL_REJECTED, {"tool": "search_flights", "reason_code": REASON_INVALID_ARGS})
        return _refusal(
            REASON_INVALID_ARGS,
            f"shift_preference must be one of {list(SHIFT_PREFERENCES)}.",
            allowed={"shift_preference": list(SHIFT_PREFERENCES)},
        )
    refusal = _validate_search_args(ctx, direction, depart_date, origin_airports, dest_airports)
    if refusal is not None:
        ctx.record(EVENT_TOOL_REJECTED, {"tool": "search_flights", "reason_code": refusal["error"]})
        return refusal

    result, candidates = _search_leg_once(
        ctx, direction, depart_date, origin_airports, dest_airports
    )
    if candidates or not _is_travellers_own_date(ctx, direction, depart_date):
        return result

    base = _leg_date(ctx.base_request, direction)
    tried = [base.isoformat()]
    for offset in _walk_offsets(ctx.max_date_shift_days, shift_preference):
        day = base + timedelta(days=offset)
        if not _keeps_trip_in_order(ctx, direction, day):
            continue
        step, step_candidates = _search_leg_once(
            ctx, direction, day.isoformat(), origin_airports, dest_airports, record=False,
        )
        if BUDGET_NOTE in step["notes"]:
            # Out of searches. Stop rather than keep asking: the cache would only
            # hand back rows it already has, which says nothing about this date.
            break
        tried.append(day.isoformat())
        if step_candidates:
            ctx.record(EVENT_AUTO_DATE_RETRY, {
                "direction": direction,
                "from_date": base.isoformat(),
                "to_date": day.isoformat(),
                "dates_searched": len(tried),
                "included_count": step["included_count"],
            })
            # Named so the model's rationale can explain the move rather than
            # discover it. `ctx.effective_dates` carries the shift into the
            # proposal and `ctx.date_shift_notes()` discloses it to the
            # traveller; neither depends on the model noticing this key.
            step["auto_retried_from_date"] = base.isoformat()
            step["dates_searched"] = tried
            return step

    ctx.record(EVENT_AUTO_DATE_RETRY, {
        "direction": direction,
        "from_date": base.isoformat(),
        "to_date": None,
        "dates_searched": len(tried),
        "included_count": 0,
    })
    result["dates_searched"] = tried
    result["note"] = (
        f"No flights on {base.isoformat()} or on the nearby dates searched "
        f"({', '.join(sorted(tried))}). Do not search these dates again."
    )
    return result


@_tool
def rank_flights(
    ctx: ToolContext,
    *,
    direction: str,
    depart_date: str | None = None,
    priority: list[str] | None = None,
    top_n: int = 3,
) -> dict:
    """Re-rank rows already seen, by a caller-chosen priority.

    Ranks over `cache.all_rows` rather than re-searching, so choosing a different
    ordering never costs a provider call. The ordering itself is still
    `domain._rank_key`: `priority` permutes precedence between named components
    and cannot invent one, drop a tiebreaker, or change what any measures.
    """
    refusal = _validate_search_args(ctx, direction, depart_date, None, None)
    if refusal is not None:
        ctx.record(EVENT_TOOL_REJECTED, {"tool": "rank_flights", "reason_code": refusal["error"]})
        return refusal

    request = _search_request(ctx, direction, depart_date, None, None)
    effective, adjustments = normalise_priority(priority)
    origin, dest = _leg_airports(request, direction)
    leg_date = _leg_date(request, direction)
    candidates = rank_leg(
        ctx.cache.all_rows, request, origin=origin, dest=dest, leg_date=leg_date,
        direction=direction, top_n=max(1, min(int(top_n), MAX_TOP_N)), priority=effective,
    )

    # `arrival_time` is inert unless the traveller stated a SOFT arrival
    # preference — the gating that keeps the default key byte-identical. Say so,
    # rather than letting a caller believe it reordered something.
    prefs = request.trip_context.flight_preferences
    if "arrival_time" in (priority or []) and not any(
        p.direction == direction and not p.hard for p in prefs.arrival_preferences
    ):
        adjustments = [*adjustments, (
            "'arrival_time' had no effect: this traveller stated no soft arrival "
            "preference for this leg, so cost decided the order."
        )]

    # Recorded only when the caller expressed a preference: storing the default
    # would make `ranking_notes` claim a choice nobody made.
    if priority:
        ctx.effective_priority[direction] = tuple(effective)

    ctx.record(EVENT_TOOL_CALLED, {
        "tool": "rank_flights", "direction": direction, "returned": len(candidates),
        "reordered": bool(priority),
    })
    return {
        "direction": direction,
        "effective_priority": list(effective),
        "priority_adjustments": adjustments,
        "rows": [candidate.model_dump(mode="json") for candidate in candidates],
    }


@_tool
def acknowledge_unmet_preference(ctx: ToolContext, *, field: str, direction: str | None = None, reason: str) -> dict:
    """Admit that one soft preference is unanimously unmet. Verified before it
    takes effect, and never changes which flights are shown — see the module
    and class docstrings for why "relax" was the wrong verb for this.

    Two fences, both unchanged from the pre-loop implementation:

    1. `PreferenceAcknowledgment.field` is a closed `Literal`, so a request to
       touch `max_stops`, budget, or accessibility cannot even be expressed.
    2. `domain.acknowledgment_is_valid()` re-confirms a real, unanimous gap
       exists. The caller's own claim that one does is never trusted.

    At most one acknowledgment per run, as before — enforced by the budget
    rather than by the shape of the call sequence.
    """
    try:
        acknowledgment = PreferenceAcknowledgment(field=field, direction=direction, reason=reason)
    except Exception as exc:  # noqa: BLE001 - a malformed proposal is a refusal, not a crash
        ctx.record(EVENT_TOOL_REJECTED, {"tool": "acknowledge_unmet_preference", "reason_code": REASON_INVALID_ARGS})
        return _refusal(
            REASON_INVALID_ARGS, str(exc),
            allowed={"field": ["avoid_red_eye", "prefer_direct", "soft_arrival_preference"]},
        )

    gaps = flight_preference_gaps(ctx.current_request, ctx.cache.all_rows)
    if not acknowledgment_is_valid(acknowledgment, gaps):
        # Same event name and wording as the pre-loop path, so existing trace
        # consumers keep working.
        ctx.record("agent_acknowledgment_rejected", {
            "field": acknowledgment.field, "direction": acknowledgment.direction,
            "reason": "no matching gap found; ignored, not applied",
        })
        return {"applied": False, "reason_code": "no_matching_gap", "gaps": gaps}

    if not ctx.budget.spend_acknowledgment():
        ctx.record(EVENT_TOOL_REJECTED, {
            "tool": "acknowledge_unmet_preference", "reason_code": REASON_BUDGET_EXHAUSTED,
        })
        return {"applied": False, "reason_code": "acknowledgment_budget_exhausted"}

    ctx.record("agent_acknowledgment_applied", {
        "field": acknowledgment.field, "direction": acknowledgment.direction,
    })
    updated = apply_acknowledgment(ctx.current_request.trip_context.flight_preferences, acknowledgment)
    context = ctx.current_request.trip_context.model_copy(update={"flight_preferences": updated})
    ctx.current_request = ctx.current_request.model_copy(update={"trip_context": context})
    ctx.acknowledgment_applied = acknowledgment
    return {"applied": True, "field": acknowledgment.field, "direction": acknowledgment.direction}


# OpenAI-format tool schemas. Hand-written dicts, not `@tool` decorators, so this
# module stays importable without langchain — see the module docstring.
TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": (
                "Search real flight inventory for one leg. Optionally shift the date by "
                f"up to {MAX_DATE_SHIFT_DAYS} days or restrict to specific airports serving "
                "the traveller's cities. Use this when a leg returns no viable options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": list(DIRECTIONS)},
                    "depart_date": {
                        "type": "string",
                        "description": (
                            "ISO date (YYYY-MM-DD) for this leg. Must be within "
                            f"{MAX_DATE_SHIFT_DAYS} days of the traveller's own date. "
                            "Leave it out (or give the traveller's own date) to search "
                            "their date: if that has no flights, nearby dates are "
                            "searched for you automatically, nearest first."
                        ),
                    },
                    "origin_airports": {"type": "array", "items": {"type": "string"}},
                    "dest_airports": {"type": "array", "items": {"type": "string"}},
                    "shift_preference": {
                        "type": "string",
                        "enum": list(SHIFT_PREFERENCES),
                        "description": (
                            "Which way the automatic nearby-date search may move this "
                            "leg. Use 'earlier' or 'later' only when the traveller gave "
                            "a reason (e.g. must be back by a date); otherwise 'either'."
                        ),
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rank_flights",
            "description": (
                "Re-rank flights already found, by a chosen priority order. Does not "
                "search again. Use this to trade off cost against directness, red-eye "
                "avoidance, seating or arrival time for this particular traveller."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": list(DIRECTIONS)},
                    "depart_date": {"type": "string"},
                    "priority": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(RANK_COMPONENTS)},
                        "description": (
                            "Ranking components in order of precedence. Any omitted are "
                            "appended in the default order, and 'cost' always applies last "
                            "as the tiebreaker."
                        ),
                    },
                    "top_n": {"type": "integer", "minimum": 1, "maximum": MAX_TOP_N},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "acknowledge_unmet_preference",
            "description": (
                "Admit that EVERY surviving flight for a leg fails the same ONE soft "
                "preference (e.g. all remaining options are red-eyes). Not for an empty "
                "leg with zero options — use search_flights with a different date or "
                "airport for that instead. This is re-verified against the real, "
                "unanimous gap and is ignored if no such gap exists. It never changes "
                "which flights are shown or their order; it only discloses the tradeoff "
                "to the traveller instead of silently dropping their wish. At most one "
                "per request."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": ["avoid_red_eye", "prefer_direct", "soft_arrival_preference"],
                    },
                    "direction": {
                        "type": "string", "enum": list(DIRECTIONS),
                        "description": "Required when field is 'soft_arrival_preference'.",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["field", "reason"],
            },
        },
    },
]


def dispatch(ctx: ToolContext, name: str, args: dict) -> dict:
    """Run one tool call by name, spending budget first.

    Never catches `SafetyError` or a guardrail exception: those are fail-closed
    by design, and swallowing one would convert a blocked request into an allowed
    one. Only argument and tool-shaped failures become refusals — the same narrow
    blast radius `reasoning.py` keeps around its model call.
    """
    tool = _TOOLS.get(name)
    if tool is None:
        ctx.record(EVENT_TOOL_REJECTED, {"tool": name, "reason_code": REASON_INVALID_ARGS})
        return _refusal(REASON_INVALID_ARGS, f"unknown tool {name!r}.", allowed={"tools": list(_TOOLS)})

    if not ctx.budget.spend_tool_call():
        ctx.record(EVENT_TOOL_REJECTED, {"tool": name, "reason_code": REASON_BUDGET_EXHAUSTED})
        return _refusal(
            REASON_BUDGET_EXHAUSTED,
            "Tool-call budget reached. Conclude using what you already have.",
        )

    try:
        return tool(ctx, **(args or {}))
    except TypeError as exc:
        # A wrong or missing argument name: the model's mistake to correct.
        ctx.record(EVENT_TOOL_REJECTED, {"tool": name, "reason_code": REASON_INVALID_ARGS})
        return _refusal(REASON_INVALID_ARGS, str(exc))
