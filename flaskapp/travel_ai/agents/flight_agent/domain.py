"""Flight Agent domain logic — pure functions, no I/O.

Deliberately decoupled from transport (LangGraph node today, event-bus
consumer later — see localfolder/discussion_20Jul2026.md) and from
persistence (inventory is passed in as plain objects). Whatever the team
lands on for either, this module should not need to change.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any

from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightCandidate,
    FlightInventoryItem,
    FlightPreferences,
    FlightProposal,
    FlightProposalRequest,
    FlightScreeningResult,
    PreferenceAcknowledgment,
    TripContext,
)

# One airport code, or every airport serving a chosen city. Route filtering
# takes either so a caller that has resolved a city and one that only knows a
# single gateway share the same functions.
AirportCodes = str | Sequence[str]

RED_EYE_DEPARTURE_HOUR = 22  # local hour and later counts as a red-eye departure
RED_EYE_ARRIVAL_HOUR = 6  # local hour before this counts as a red-eye arrival

WHEELCHAIR_HINTS = ("wheelchair",)


def needs_wheelchair(accessibility_needs: list[str]) -> bool:
    """Whether any stated need indicates wheelchair assistance.

    Substring matching, not equality. The intake form takes free text, so real
    entries read "wheelchair assistance" or "wheelchair user" — an exact match
    on the bare token silently missed every one of them, disabling the
    accessibility filter for precisely the travellers it exists to protect.
    Entries arrive already stripped of the form's "Traveler 2: " prefix by
    `adapter.strip_traveller_prefix`.
    """
    return any(hint in need.lower() for need in accessibility_needs for hint in WHEELCHAIR_HINTS)


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _as_codes(value: AirportCodes | None) -> tuple[str, ...]:
    """Normalise one airport code or several into a tuple.

    Callers pass a list once a city has been resolved (Tokyo -> NRT, HND) and a
    bare string from anywhere predating city intake — golden scenarios, unit
    tests, a renegotiation that names one gateway. Accepting both keeps every
    existing call site working untouched.
    """
    if value is None:
        return ()
    return (value,) if isinstance(value, str) else tuple(value)


def _matches_route(item: FlightInventoryItem, origin: AirportCodes, dest: AirportCodes) -> bool:
    """Whether this flight serves any origin airport -> any destination airport.

    Membership, not equality: a traveller who chose Tokyo should see flights
    into Haneda and Narita both, and comparing against a single code here is
    exactly the single-gateway limitation that collecting a city removes.
    """
    return item.origin_airport in _as_codes(origin) and item.dest_airport in _as_codes(dest)


def _party_size(ctx: TripContext) -> int:
    adults = ctx.party.get("adults", 1) or 0
    children = ctx.party.get("children", 0) or 0
    return max(adults + children, 1)


def _is_red_eye(item: FlightInventoryItem) -> bool:
    """Illustrative simplification (db_schema.md rule 3): a late-night
    departure or a pre-dawn arrival, by local clock time at each end."""
    return (
        _parse_ts(item.dep_ts).hour >= RED_EYE_DEPARTURE_HOUR
        or _parse_ts(item.arr_ts).hour < RED_EYE_ARRIVAL_HOUR
    )


def _hard_arrival_preference(prefs: FlightPreferences, direction: str):
    for pref in prefs.arrival_preferences:
        if pref.direction == direction and pref.hard:
            return pref
    return None


def _soft_arrival_preference(prefs: FlightPreferences, direction: str):
    for pref in prefs.arrival_preferences:
        if pref.direction == direction and not pref.hard:
            return pref
    return None


def _preference_failure_reasons(item: FlightInventoryItem, prefs: FlightPreferences, direction: str) -> list[str]:
    """Hard preference violations only — soft preferences (prefer_direct,
    avoid_red_eye, a non-hard arrival_preference) never exclude, they only
    affect ranking; see `_rank_key`."""
    reasons: list[str] = []
    if prefs.max_stops is not None and item.stops > prefs.max_stops:
        reasons.append(f"{item.stops} stop(s) exceeds preference max_stops {prefs.max_stops}")
    hard_pref = _hard_arrival_preference(prefs, direction)
    if hard_pref is not None and _parse_ts(item.arr_ts) > _parse_ts(hard_pref.by):
        reasons.append(f"arrival {item.arr_ts} later than traveller's required arrival {hard_pref.by}")
    return reasons


def _selecting_seats(prefs: FlightPreferences) -> bool:
    return prefs.must_sit_together or prefs.seat_configuration is not None or prefs.seat_tier_preference is not None


def seat_fee_estimate(item: FlightInventoryItem, prefs: FlightPreferences, party_size: int) -> float:
    """Total seat-selection fee for the whole party (0 if not selecting seats,
    or if this flight doesn't model seat inventory). Uses the requested tier's
    fee when that tier can seat the whole party, else falls back to the
    standard-selection fee (a soft demotion, not an exclusion).

    Known simplification: accessible seats are free, but this does not subtract
    a free accessible seat when a wheelchair party also opts into paid
    selection for its other members — it would overcount by at most one seat.
    Per-person seat accounting isn't modelled (we only have trip-level
    accessibility_needs), so this is deliberately left as a documented rounding.
    """
    si = item.seat_inventory
    if si is None or not _selecting_seats(prefs):
        return 0.0
    tier = prefs.seat_tier_preference or "STANDARD"
    if tier == "EXTRA_LEGROOM" and si.extra_legroom_available >= party_size:
        per_seat = si.extra_legroom_fee
    elif tier == "EXIT_ROW" and si.exit_row_available >= party_size:
        per_seat = si.exit_row_fee
    else:
        per_seat = si.standard_fee
    return per_seat * party_size


def _seat_config_unsatisfiable(item: FlightInventoryItem, prefs: FlightPreferences) -> bool:
    """True if a stated seat_configuration can't be met from this flight's
    counts. Aggregate check only (see SeatInventory docstring) — confirms the
    counts exist, not that they're geometrically arrangeable."""
    config = prefs.seat_configuration
    if config is None:
        return False
    si = item.seat_inventory
    if si is None:
        return True  # unmodelled flight can't be shown to satisfy a config
    return si.window_available < config.get("window", 0) or si.aisle_available < config.get("aisle", 0)


def _effective_cost(item: FlightInventoryItem, prefs: FlightPreferences, party_size: int) -> float:
    """Whole-party flight cost plus seat fees — what ranking sorts on so that
    a cheaper base fare with pricey seats can lose to a dearer fare with free
    seats. For party_size=1 with no seat selection this equals item.price, so
    pre-seat ranking is unchanged."""
    return item.price * party_size + seat_fee_estimate(item, prefs, party_size)


# --- Ranking components -------------------------------------------------------
#
# The rank key is lexicographic, not a blended score — each position is
# individually nameable, so ranking stays explainable instead of an opaque
# weighted number nobody can justify. `agent.py:_candidate_to_option` restates
# these as `selection_factors`, which is only honest because this key is what
# actually produced the order.
#
# They are split into named components so the ORDER can be a parameter without
# the components themselves ever becoming one. A caller (including the
# tool-calling loop, via `tools.rank_flights`) may permute precedence; nobody can
# invent a component, drop a tiebreaker, or change what any component measures.


def _component_accessibility_verified(item, prefs, direction, party_size, needs_assist) -> int:
    """Unverified accessibility sorts last for the traveller who stated the need.

    Leads the default order because it outranks every other consideration for
    the traveller it applies to: a wheelchair user given a flight whose
    assistance is confirmed beats a cheaper or better-timed flight whose
    assistance is merely unknown. Inert for everyone else, and inert for seed
    inventory (every seed row states a real True/False).
    """
    return 1 if (needs_assist and item.wheelchair_assist_available is None) else 0


def _component_avoid_red_eye(item, prefs, direction, party_size, needs_assist) -> int:
    return 1 if (prefs.avoid_red_eye and _is_red_eye(item)) else 0


def _component_prefer_direct(item, prefs, direction, party_size, needs_assist) -> int:
    return 1 if (prefs.prefer_direct and item.stops > 0) else 0


def _component_seat_config(item, prefs, direction, party_size, needs_assist) -> int:
    return 1 if _seat_config_unsatisfiable(item, prefs) else 0


def _component_arrival_time(item, prefs, direction, party_size, needs_assist):
    """Arrival time, but only when a soft arrival preference exists — otherwise a
    constant, so this position cannot reorder anything for travellers who never
    expressed one."""
    soft_pref = _soft_arrival_preference(prefs, direction)
    return _parse_ts(item.arr_ts) if soft_pref is not None else datetime.min


def _component_cost(item, prefs, direction, party_size, needs_assist) -> float:
    return _effective_cost(item, prefs, party_size)


RANK_COMPONENTS: dict[str, Callable[..., Any]] = {
    "accessibility_verified": _component_accessibility_verified,
    "avoid_red_eye": _component_avoid_red_eye,
    "prefer_direct": _component_prefer_direct,
    "seat_config": _component_seat_config,
    "arrival_time": _component_arrival_time,
    "cost": _component_cost,
}

# Exactly the order the hand-written tuple used before this was parameterised.
# `propose_flights` never passes `priority`, so its key is byte-identical and the
# golden scenarios keep asserting the same ordered id lists.
DEFAULT_RANK_PRIORITY: tuple[str, ...] = (
    "accessibility_verified",
    "avoid_red_eye",
    "prefer_direct",
    "seat_config",
    "arrival_time",
    "cost",
)

# `cost` is the only always-defined continuous component, so it is the only one
# that reliably breaks a tie. Forcing it terminal guarantees a total order under
# any permutation — without it, a caller asking for cost first would make every
# later component dead weight and could leave genuine ties resolved by list
# order, which is not stable across inventory refreshes.
_TERMINAL_COMPONENT = "cost"


def normalise_priority(
    priority: Sequence[str] | None,
) -> tuple[tuple[str, ...], list[str]]:
    """A caller-supplied ranking order, made safe.

    Returns the effective order and human-readable notes about anything that was
    adjusted, so a caller can be told what actually happened rather than silently
    getting something other than it asked for.

    Three rules, all enforced here rather than trusted to the caller:

    1. **Unknown names are dropped, not raised on.** This is reachable from a
       model-supplied argument, and a bad name should cost ranking precision, not
       crash the request.
    2. **Omitted components are appended in default order, never dropped.** The
       key is always a total order over every component, so a permutation can
       reorder results but can never make ranking nondeterministic.
    3. **`cost` is always terminal**, wherever the caller put it.
    """
    if priority is None:
        return DEFAULT_RANK_PRIORITY, []

    notes: list[str] = []
    effective: list[str] = []
    for name in priority:
        if name not in RANK_COMPONENTS:
            notes.append(f"Ignored unknown ranking component {name!r}.")
            continue
        if name in effective or name == _TERMINAL_COMPONENT:
            continue
        effective.append(name)

    if _TERMINAL_COMPONENT in priority and list(priority)[-1] != _TERMINAL_COMPONENT:
        notes.append(
            f"Moved {_TERMINAL_COMPONENT!r} to last: it is the tiebreaker that "
            "guarantees a stable total order."
        )

    omitted = [
        name for name in DEFAULT_RANK_PRIORITY
        if name not in effective and name != _TERMINAL_COMPONENT
    ]
    if omitted:
        notes.append(
            "Appended omitted components in default order so ranking stays a "
            f"total order: {', '.join(omitted)}."
        )
    effective.extend(omitted)
    effective.append(_TERMINAL_COMPONENT)
    return tuple(effective), notes


def _rank_key(
    item: FlightInventoryItem,
    prefs: FlightPreferences,
    direction: str,
    party_size: int,
    *,
    needs_wheelchair_assist: bool = False,
    priority: Sequence[str] = DEFAULT_RANK_PRIORITY,
) -> tuple:
    """The lexicographic sort key. See `RANK_COMPONENTS` for what each position
    means and `normalise_priority` for why `priority` cannot make it unsafe.

    `priority` is assumed already normalised; `rank_leg` is the caller that
    normalises. Every item in one sort uses the same order, so positions are
    type-consistent and comparable.
    """
    return tuple(
        RANK_COMPONENTS[name](item, prefs, direction, party_size, needs_wheelchair_assist)
        for name in priority
    )


def _seat_failure_reasons(item: FlightInventoryItem, request: FlightProposalRequest) -> list[str]:
    """Hard seat exclusions — only engage when the flight models seat
    inventory. Soft seat preferences (seat_configuration, tier) never appear
    here; they only affect ranking / fees."""
    si = item.seat_inventory
    if si is None:
        return []
    reasons: list[str] = []
    prefs = request.trip_context.flight_preferences
    party_size = _party_size(request.trip_context)
    needs_wheelchair_assist = needs_wheelchair(request.trip_context.accessibility_needs)
    if prefs.must_sit_together and si.max_adjacent_block < party_size:
        reasons.append(
            f"largest adjacent seat block is {si.max_adjacent_block}, "
            f"party of {party_size} requested to sit together"
        )
    if needs_wheelchair_assist and si.accessible_available < 1:
        reasons.append("no accessible seat available but accessibility_needs includes wheelchair")
    if needs_wheelchair_assist and prefs.seat_tier_preference == "EXIT_ROW":
        reasons.append("exit-row seats are unavailable to passengers requiring wheelchair assistance")
    return reasons


def _constraint_failure_reasons(
    item: FlightInventoryItem, request: FlightProposalRequest, direction: str
) -> list[str]:
    constraints = request.constraints
    applies_to_this_leg = constraints is not None and constraints.direction in (None, direction)
    reasons: list[str] = []
    if applies_to_this_leg:
        # Fail-closed on unknown here, unlike the implicit filter below. An
        # explicit require_wheelchair_assist is orchestrator-issued mid-
        # negotiation — typically because Accessibility Agent already vetoed a
        # round — so "the supplier doesn't publish it" is not good enough to
        # satisfy it. Unverified is not met.
        if constraints.require_wheelchair_assist and item.wheelchair_assist_available is not True:
            reasons.append(
                "constraint require_wheelchair_assist not met"
                if item.wheelchair_assist_available is False
                else "constraint require_wheelchair_assist cannot be verified for this flight"
            )
        if constraints.max_price is not None and item.price > constraints.max_price:
            reasons.append(f"price {item.price} exceeds constraint max_price {constraints.max_price}")
        if constraints.arrive_before is not None and _parse_ts(item.arr_ts) > _parse_ts(constraints.arrive_before):
            reasons.append(f"arrival {item.arr_ts} later than constraint arrive_before {constraints.arrive_before}")
        return reasons
    # No constraint targets this leg (either none supplied, or this round's
    # constraint is scoped to the other leg) — fall back to the implicit
    # proactive filter. Accessibility Agent still holds veto authority
    # end-to-end regardless of what Flight filters here.
    #
    # `None` (source doesn't publish the field) deliberately does NOT exclude.
    # Excluding on unknown would return zero options to every wheelchair user
    # the moment inventory comes from a live feed, which reads as "no flights
    # exist for you" rather than the truth, "we could not check". The flight
    # stays in, carries `wheelchair_assist_available=None` through to the
    # candidate, and agent.py turns that into an explicit unverified
    # limitation on the traveller-facing option. Accessibility Agent's veto is
    # the backstop, and an explicit constraint above is fail-closed.
    needs_wheelchair_assist = needs_wheelchair(request.trip_context.accessibility_needs)
    if needs_wheelchair_assist and item.wheelchair_assist_available is False:
        reasons.append("accessibility_needs includes wheelchair but wheelchair_assist_available is False")
    return reasons


def _screen_item(
    item: FlightInventoryItem, request: FlightProposalRequest, direction: str, leg_date: date
) -> list[str]:
    """Every reason this inventory row would be excluded from the proposal.

    Empty list means it's included. Route matching is assumed already done by
    the caller (a different route is out of scope for this leg entirely, not
    a "reason" worth recording).
    """
    reasons: list[str] = []
    dep_date = _parse_ts(item.dep_ts).date()
    if dep_date != leg_date:
        reasons.append(f"departs {dep_date}, requested {leg_date}")
    party_size = _party_size(request.trip_context)
    if item.seats_available < party_size:
        reasons.append(f"only {item.seats_available} seat(s) available, party needs {party_size}")
    reasons.extend(_constraint_failure_reasons(item, request, direction))
    reasons.extend(_preference_failure_reasons(item, request.trip_context.flight_preferences, direction))
    reasons.extend(_seat_failure_reasons(item, request))
    return reasons


def screen_leg(
    inventory: list[FlightInventoryItem],
    request: FlightProposalRequest,
    *,
    origin: AirportCodes,
    dest: AirportCodes,
    leg_date: date,
    direction: str,
) -> list[FlightScreeningResult]:
    """Post-tool traceability: why every same-route inventory row was or
    wasn't included, not just the survivors. For the explainability log /
    drill-down panel, not for ranking (see `propose_flights`)."""
    results = []
    for item in inventory:
        if not _matches_route(item, origin, dest):
            continue
        reasons = _screen_item(item, request, direction, leg_date)
        results.append(
            FlightScreeningResult(
                flight_id=item.flight_id,
                direction=direction,
                included=not reasons,
                reasons=reasons,
            )
        )
    return results


def _survivors_for_leg(
    inventory: list[FlightInventoryItem],
    request: FlightProposalRequest,
    *,
    origin: AirportCodes,
    dest: AirportCodes,
    leg_date: date,
    direction: str,
) -> list[FlightInventoryItem]:
    """Items passing every hard filter (route, date, seats, constraints,
    hard preferences) for this leg — unsorted, before top_n. Shared by
    ranking (`rank_leg`) and the option-B acknowledgment-gap check
    (`preference_gap_for_leg`) so the two never drift apart."""
    return [
        item
        for item in inventory
        if _matches_route(item, origin, dest) and not _screen_item(item, request, direction, leg_date)
    ]


def preference_gap_for_leg(
    inventory: list[FlightInventoryItem],
    request: FlightProposalRequest,
    *,
    origin: AirportCodes,
    dest: AirportCodes,
    leg_date: date,
    direction: str,
) -> list[str]:
    """Which soft preferences every hard-filter survivor unanimously still
    violates — i.e. where acknowledging the preference as unmet is at least
    honest, even though it changes nothing about which flights are shown.
    Empty list means either nothing violates, or (deliberately) there are no
    survivors at all: an empty leg is a hard-constraint problem (accessibility,
    seats, dates, max_stops, a hard arrival deadline), never something Flight
    Agent's own soft-preference acknowledgment (option B) is allowed to touch —
    that must go to escalation instead, see localfolder/discussion_agents_vs_deterministic.md.
    """
    survivors = _survivors_for_leg(inventory, request, origin=origin, dest=dest, leg_date=leg_date, direction=direction)
    if not survivors:
        return []
    prefs = request.trip_context.flight_preferences
    gaps: list[str] = []
    if prefs.avoid_red_eye and all(_is_red_eye(item) for item in survivors):
        gaps.append("avoid_red_eye")
    if prefs.prefer_direct and all(item.stops > 0 for item in survivors):
        gaps.append("prefer_direct")
    soft_pref = _soft_arrival_preference(prefs, direction)
    if soft_pref is not None and all(_parse_ts(item.arr_ts) > _parse_ts(soft_pref.by) for item in survivors):
        gaps.append("soft_arrival_preference")
    return gaps


def flight_preference_gaps(request: FlightProposalRequest, inventory: list[FlightInventoryItem]) -> dict[str, list[str]]:
    """Both legs' acknowledgment gaps — what agent.py shows the LLM so it
    knows what's actually acknowledgeable (if anything) before proposing
    option B."""
    ctx = request.trip_context
    return {
        "OUTBOUND": preference_gap_for_leg(
            inventory, request,
            origin=ctx.origin_airports, dest=ctx.dest_airports,
            leg_date=_parse_date(ctx.depart_date), direction="OUTBOUND",
        ),
        "RETURN": preference_gap_for_leg(
            inventory, request,
            origin=ctx.dest_airports, dest=ctx.origin_airports,
            leg_date=_parse_date(ctx.return_date), direction="RETURN",
        ),
    }


def acknowledgment_is_valid(acknowledgment: PreferenceAcknowledgment, gaps: dict[str, list[str]]) -> bool:
    """Code-side re-verification that a proposed acknowledgment corresponds
    to a real, currently-existing gap — never trust the LLM's own claim that
    a preference actually went unmet. This is what keeps option B bounded: an
    LLM can *ask* to acknowledge avoid_red_eye/prefer_direct/soft_arrival_preference
    as unmet, but the request only ever takes effect if this returns True."""
    directions = [acknowledgment.direction] if acknowledgment.direction else ["OUTBOUND", "RETURN"]
    return any(acknowledgment.field in gaps.get(d, []) for d in directions)


def apply_acknowledgment(prefs: FlightPreferences, acknowledgment: PreferenceAcknowledgment) -> FlightPreferences:
    """A new FlightPreferences with exactly one soft preference turned off —
    never touches max_stops or any hard arrival_preference (those aren't
    valid `PreferenceAcknowledgment.field` values at the schema level, so this
    can't accidentally reach a hard constraint).

    Turning the flag off here does NOT change which flights are shown for the
    gap that justified this call — every survivor already failed it unanimously,
    so there was nothing left for the flag to differentiate. It only changes
    ranking behaviour for whatever the loop searches next (a later, mixed
    result set could genuinely be affected by it)."""
    update: dict = {}
    if acknowledgment.field == "avoid_red_eye":
        update["avoid_red_eye"] = False
    elif acknowledgment.field == "prefer_direct":
        update["prefer_direct"] = False
    elif acknowledgment.field == "soft_arrival_preference":
        update["arrival_preferences"] = [
            pref
            for pref in prefs.arrival_preferences
            if pref.hard or pref.direction != acknowledgment.direction
        ]
    return prefs.model_copy(update=update)


def rank_leg(
    inventory: list[FlightInventoryItem],
    request: FlightProposalRequest,
    *,
    origin: AirportCodes,
    dest: AirportCodes,
    leg_date: date,
    direction: str,
    top_n: int = 3,
    priority: Sequence[str] | None = None,
) -> list[FlightCandidate]:
    """Hard-filter survivors for one leg, ranked and truncated to `top_n`.

    Public because ranking is callable on its own — the same survivors can be
    ordered by different priorities without re-running the search. `priority` of
    `None` means `DEFAULT_RANK_PRIORITY`, which is what `propose_flights` passes,
    so the default path produces byte-identical output to before this was
    parameterised.

    Use `normalise_priority` first if you need to report back what was adjusted;
    this normalises internally and discards the notes.
    """
    effective, _notes = normalise_priority(priority)
    matches = _survivors_for_leg(inventory, request, origin=origin, dest=dest, leg_date=leg_date, direction=direction)
    prefs = request.trip_context.flight_preferences
    party_size = _party_size(request.trip_context)
    needs_assist = needs_wheelchair(request.trip_context.accessibility_needs)
    matches.sort(
        key=lambda item: _rank_key(
            item, prefs, direction, party_size,
            needs_wheelchair_assist=needs_assist, priority=effective,
        )
    )
    return [
        FlightCandidate(
            flight_id=item.flight_id,
            direction=direction,
            dep_ts=item.dep_ts,
            arr_ts=item.arr_ts,
            dest_airport=item.dest_airport,
            stops=item.stops,
            price=item.price,
            seats=item.seats_available,
            wheelchair_assist_available=item.wheelchair_assist_available,
            step_free_boarding=item.step_free_boarding,
            seat_fee_estimate=seat_fee_estimate(item, prefs, party_size),
            source=item.source,
        )
        for item in matches[:top_n]
    ]


def propose_flights(
    request: FlightProposalRequest,
    inventory: list[FlightInventoryItem],
    *,
    top_n: int = 3,
) -> FlightProposal:
    """Rank outbound + return candidates from `inventory` for one request.

    Returns an empty candidate list per leg (not an error) when nothing
    survives filtering — the caller/orchestrator decides whether that's a
    renegotiation trigger or an escalation. Call `screen_leg` separately if
    you need the reasons rejected flights didn't make it (explainability log).
    """
    ctx = request.trip_context
    outbound = rank_leg(
        inventory,
        request,
        origin=ctx.origin_airports,
        dest=ctx.dest_airports,
        leg_date=_parse_date(ctx.depart_date),
        direction="OUTBOUND",
        top_n=top_n,
    )
    inbound = rank_leg(
        inventory,
        request,
        origin=ctx.dest_airports,
        dest=ctx.origin_airports,
        leg_date=_parse_date(ctx.return_date),
        direction="RETURN",
        top_n=top_n,
    )
    return FlightProposal(candidates=outbound + inbound)


def screen_flights(
    request: FlightProposalRequest, inventory: list[FlightInventoryItem]
) -> list[FlightScreeningResult]:
    """Both legs' full screening trace (included + excluded) — the
    explainability companion to `propose_flights`, same request shape."""
    ctx = request.trip_context
    outbound = screen_leg(
        inventory,
        request,
        origin=ctx.origin_airports,
        dest=ctx.dest_airports,
        leg_date=_parse_date(ctx.depart_date),
        direction="OUTBOUND",
    )
    inbound = screen_leg(
        inventory,
        request,
        origin=ctx.dest_airports,
        dest=ctx.origin_airports,
        leg_date=_parse_date(ctx.return_date),
        direction="RETURN",
    )
    return outbound + inbound
