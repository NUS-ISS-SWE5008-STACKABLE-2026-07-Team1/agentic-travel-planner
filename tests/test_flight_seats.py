"""Seat selection (discussion_agents_vs_deterministic.md §7).

Purpose-built flights with explicit SeatInventory so these don't depend on
the randomly-generated seed data. Covers the two flagship scenarios (wheelchair
user without a suitable seat; family that can't sit together), the fee model,
and the "accessible seats are free" bias decision.
"""

from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights, screen_flights, seat_fee_estimate
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightInventoryItem,
    FlightPreferences,
    FlightProposalRequest,
    SeatInventory,
    TripContext,
)

BASE_CONTEXT = dict(
    origin_airport="SIN",
    dest_airport="NRT",
    dest_city="Tokyo",
    dest_country="JP",
    depart_date="2026-09-01",
    return_date="2026-09-05",
    passport_country="SG",
)


def _seats(**overrides) -> SeatInventory:
    base = dict(
        window_available=5,
        aisle_available=5,
        max_adjacent_block=6,
        accessible_available=2,
        standard_fee=15.0,
        extra_legroom_available=4,
        extra_legroom_fee=50.0,
        exit_row_available=4,
        exit_row_fee=70.0,
    )
    base.update(overrides)
    return SeatInventory(**base)


def _flight(flight_id, price, seat_inventory, dest="NRT", origin="SIN", dep="2026-09-01T09:00+08:00", arr="2026-09-01T16:00+09:00"):
    return FlightInventoryItem(
        flight_id=flight_id, carrier="SQ", flight_no=flight_id,
        origin_airport=origin, dest_airport=dest,
        dep_ts=dep, arr_ts=arr, duration_min=400, price=price,
        cabin_class="ECONOMY", seats_available=20, stops=0,
        wheelchair_assist_available=True, step_free_boarding=True,
        seat_inventory=seat_inventory,
    )


# A return flight so both legs have options (RETURN never carries seat prefs here)
RET = _flight("RET1", 400, _seats(), dest="SIN", origin="NRT",
              dep="2026-09-05T11:00+09:00", arr="2026-09-05T17:00+08:00")


def _request(prefs, **ctx_overrides):
    ctx = {**BASE_CONTEXT, **ctx_overrides, "flight_preferences": prefs}
    return FlightProposalRequest(trip_context=TripContext(**ctx))


def _outbound(proposal):
    return [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]


# --- Scenario A: wheelchair user without a suitable seat ---

def test_wheelchair_excludes_flight_with_no_accessible_seat():
    with_seat = _flight("HAS_ACC", 300, _seats(accessible_available=1))
    without_seat = _flight("NO_ACC", 250, _seats(accessible_available=0))
    request = _request(FlightPreferences(), accessibility_needs=["wheelchair"])
    proposal = propose_flights(request, [with_seat, without_seat, RET])
    assert _outbound(proposal) == ["HAS_ACC"]  # cheaper NO_ACC excluded

    screening = screen_flights(request, [with_seat, without_seat, RET])
    no_acc = next(r for r in screening if r.flight_id == "NO_ACC")
    assert "no accessible seat" in no_acc.reasons[0]


def test_wheelchair_user_cannot_book_exit_row():
    f = _flight("F", 300, _seats(accessible_available=2))
    request = _request(
        FlightPreferences(seat_tier_preference="EXIT_ROW"),
        accessibility_needs=["wheelchair"],
    )
    proposal = propose_flights(request, [f, RET])
    assert "F" not in _outbound(proposal)
    screening = screen_flights(request, [f, RET])
    reason = next(r for r in screening if r.flight_id == "F").reasons
    assert any("exit-row" in x for x in reason)


# --- Scenario B: family that must sit together ---

def test_must_sit_together_excludes_fragmented_flight():
    together = _flight("TOGETHER", 500, _seats(max_adjacent_block=4))
    split = _flight("SPLIT", 300, _seats(max_adjacent_block=2))
    request = _request(FlightPreferences(must_sit_together=True), party={"adults": 4, "children": 0})
    proposal = propose_flights(request, [together, split, RET])
    assert _outbound(proposal) == ["TOGETHER"]  # cheaper SPLIT can't seat 4 together

    screening = screen_flights(request, [together, split, RET])
    split_reason = next(r for r in screening if r.flight_id == "SPLIT").reasons
    assert any("adjacent" in x for x in split_reason)


# --- Fee model + budget exposure ---

def test_seat_fee_zero_when_not_selecting_seats():
    f = _flight("F", 300, _seats())
    assert seat_fee_estimate(f, FlightPreferences(), party_size=2) == 0.0


def test_seat_fee_charges_standard_per_seat_when_selecting():
    f = _flight("F", 300, _seats(standard_fee=15.0))
    fee = seat_fee_estimate(f, FlightPreferences(must_sit_together=True), party_size=4)
    assert fee == 60.0  # 15 * 4


def test_seat_fee_uses_requested_tier_when_available():
    f = _flight("F", 300, _seats(extra_legroom_available=4, extra_legroom_fee=50.0))
    fee = seat_fee_estimate(f, FlightPreferences(seat_tier_preference="EXTRA_LEGROOM"), party_size=2)
    assert fee == 100.0  # 50 * 2


def test_seat_fee_falls_back_to_standard_when_tier_unavailable():
    f = _flight("F", 300, _seats(extra_legroom_available=1, extra_legroom_fee=50.0, standard_fee=15.0))
    fee = seat_fee_estimate(f, FlightPreferences(seat_tier_preference="EXTRA_LEGROOM"), party_size=4)
    assert fee == 60.0  # only 1 xlegroom seat, party of 4 -> standard 15 * 4


def test_candidate_exposes_seat_fee_estimate_for_budget():
    f = _flight("F", 300, _seats(standard_fee=20.0))
    request = _request(FlightPreferences(must_sit_together=True), party={"adults": 2, "children": 0})
    proposal = propose_flights(request, [f, RET])
    fc = next(c for c in proposal.candidates if c.flight_id == "F")
    assert fc.seat_fee_estimate == 40.0  # 20 * 2


def test_accessible_seat_is_free_no_fee_from_wheelchair_need_alone():
    # Wheelchair need present but no paid tier / selection -> zero seat fee.
    # This is the "accessible seats are always free" bias decision in code.
    f = _flight("F", 300, _seats(accessible_available=2))
    fee = seat_fee_estimate(f, FlightPreferences(), party_size=1)
    assert fee == 0.0


# --- Ranking: seat fees change effective order ---

def test_seat_fees_can_flip_ranking_vs_base_fare():
    # CHEAP base fare but expensive seat selection; PRICEY base but free seats.
    cheap_pricey_seats = _flight("CHEAP", 300, _seats(standard_fee=80.0))
    pricey_free_seats = _flight("PRICEY", 350, _seats(standard_fee=0.0))
    request = _request(FlightPreferences(must_sit_together=True), party={"adults": 2, "children": 0})
    proposal = propose_flights(request, [cheap_pricey_seats, pricey_free_seats, RET])
    # CHEAP effective = 300*2 + 80*2 = 760; PRICEY = 350*2 + 0 = 700 -> PRICEY first
    assert _outbound(proposal) == ["PRICEY", "CHEAP"]


# --- Backward compatibility ---

def test_flight_without_seat_inventory_skips_all_seat_logic():
    no_seat_model = FlightInventoryItem(
        flight_id="LEGACY", carrier="SQ", flight_no="LEGACY",
        origin_airport="SIN", dest_airport="NRT",
        dep_ts="2026-09-01T09:00+08:00", arr_ts="2026-09-01T16:00+09:00",
        duration_min=400, price=300, cabin_class="ECONOMY", seats_available=20,
        stops=0, wheelchair_assist_available=True, step_free_boarding=True,
        # seat_inventory omitted -> None
    )
    # Even with wheelchair need + must_sit_together, a non-seat-modeled flight
    # isn't excluded by seat rules, and carries no seat fee.
    request = _request(
        FlightPreferences(must_sit_together=True),
        accessibility_needs=["wheelchair"], party={"adults": 4, "children": 0},
    )
    proposal = propose_flights(request, [no_seat_model, RET])
    assert "LEGACY" in _outbound(proposal)
    assert seat_fee_estimate(no_seat_model, request.trip_context.flight_preferences, 4) == 0.0
