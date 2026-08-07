from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights, screen_flights
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    ArrivalPreference,
    FlightInventoryItem,
    FlightPreferences,
    FlightProposalRequest,
    TripContext,
)

BASE_CONTEXT = dict(
    origin_airport="SIN",
    dest_airport="NRT",
    dest_city="Tokyo",
    dest_country="JP",
    depart_date="2026-09-01",
    return_date="2026-09-05",
    party={"adults": 1, "children": 0},
    passport_country="SG",
)


def _flight(flight_id, dep_ts, arr_ts, price, stops=0):
    return FlightInventoryItem(
        flight_id=flight_id,
        carrier="SQ",
        flight_no=flight_id,
        origin_airport="SIN",
        dest_airport="NRT",
        dep_ts=dep_ts,
        arr_ts=arr_ts,
        duration_min=400,
        price=price,
        cabin_class="ECONOMY",
        seats_available=10,
        stops=stops,
        wheelchair_assist_available=True,
        step_free_boarding=True,
    )


# F1: direct, arrives 15:10, mid price
F1 = _flight("F1", "2026-09-01T08:00+08:00", "2026-09-01T15:10+09:00", 500)
# F2: direct but red-eye (dep >=22:00, arr <06:00 next day), cheapest
F2 = _flight("F2", "2026-09-01T23:00+08:00", "2026-09-02T05:00+09:00", 300)
# F3: 1-stop, arrives 20:00
F3 = _flight("F3", "2026-09-01T10:00+08:00", "2026-09-01T20:00+09:00", 350, stops=1)
# F4: direct, earliest arrival (13:00), most expensive
F4 = _flight("F4", "2026-09-01T06:00+08:00", "2026-09-01T13:00+09:00", 600)

INVENTORY = [F1, F2, F3, F4]


def _request(flight_preferences: FlightPreferences) -> FlightProposalRequest:
    return FlightProposalRequest(
        trip_context=TripContext(**BASE_CONTEXT, flight_preferences=flight_preferences)
    )


def _outbound_ids(proposal):
    return [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]


def test_default_preferences_reproduce_price_only_ranking():
    """Backward-compat guarantee: default FlightPreferences() must rank
    identically to the old plain-price sort."""
    proposal = propose_flights(_request(FlightPreferences()), INVENTORY, top_n=4)
    assert _outbound_ids(proposal) == ["F2", "F3", "F1", "F4"]  # 300, 350, 500, 600


def test_max_stops_hard_filter_excludes_over_limit():
    request = _request(FlightPreferences(max_stops=0))
    proposal = propose_flights(request, INVENTORY, top_n=4)
    assert "F3" not in _outbound_ids(proposal)

    screening = screen_flights(request, INVENTORY)
    f3 = next(r for r in screening if r.flight_id == "F3" and r.direction == "OUTBOUND")
    assert f3.included is False
    assert "max_stops" in f3.reasons[0]


def test_hard_arrival_preference_excludes_late_options():
    prefs = FlightPreferences(
        arrival_preferences=[ArrivalPreference(direction="OUTBOUND", by="2026-09-01T14:00+09:00", hard=True)]
    )
    proposal = propose_flights(_request(prefs), INVENTORY, top_n=4)
    assert _outbound_ids(proposal) == ["F4"]  # only F4 arrives by 14:00


def test_soft_arrival_preference_ranks_earliest_first_without_excluding():
    prefs = FlightPreferences(
        arrival_preferences=[ArrivalPreference(direction="OUTBOUND", by="2026-09-01T12:00+09:00", hard=False)]
    )
    proposal = propose_flights(_request(prefs), INVENTORY, top_n=4)
    # nothing excluded (soft), ranked purely by arrival time ascending
    assert _outbound_ids(proposal) == ["F4", "F1", "F3", "F2"]


def test_avoid_red_eye_ranks_red_eye_last_without_excluding():
    proposal = propose_flights(_request(FlightPreferences(avoid_red_eye=True)), INVENTORY, top_n=4)
    ids = _outbound_ids(proposal)
    assert set(ids) == {"F1", "F2", "F3", "F4"}  # nothing excluded
    assert ids[-1] == "F2"  # the red-eye, despite being cheapest, ranks last
    assert ids[:3] == ["F3", "F1", "F4"]  # remaining three by price


def test_prefer_direct_ranks_connecting_flight_last_without_excluding():
    proposal = propose_flights(_request(FlightPreferences(prefer_direct=True)), INVENTORY, top_n=4)
    ids = _outbound_ids(proposal)
    assert set(ids) == {"F1", "F2", "F3", "F4"}  # nothing excluded
    assert ids[-1] == "F3"  # the 1-stop option ranks last regardless of its price
    assert ids[:3] == ["F2", "F1", "F4"]  # remaining three (all direct) by price
