"""Two different questions, two different figures, both labelled.

A tier column answers "what does this flight cost?" — a fare, per seat, which
is how every airline quotes one and the only figure comparable across the three
columns. "Closest to your budget" answers "can this party afford the trip?",
which is the whole-party total.

The same number in both places would be wrong in one of them, so the Option
carries both and each renderer says which it is showing.
"""

from flaskapp.travel_ai.agents.flight_agent.agent import _candidate_to_option
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightCandidate
from flaskapp.travel_ai.recommendation import recommend_package
from flaskapp.travel_ai.schemas import AgentFinding, Option, TravelRequest


def candidate(price=600.0, seat_fee=0.0, fid="JL4001-20261010", direction="OUTBOUND"):
    return FlightCandidate(
        flight_id=fid, direction=direction,
        dep_ts="2026-10-10T09:00+08:00", arr_ts="2026-10-10T17:00+09:00",
        dest_airport="NRT", stops=0, price=price, seats=9,
        wheelchair_assist_available=None, step_free_boarding=None,
        seat_fee_estimate=seat_fee,
    )


def test_a_flight_carries_both_the_party_total_and_one_fare():
    option = _candidate_to_option(candidate(price=600), "SGD", "a", party_size=3)
    assert option.estimated_cost == 1800   # what the party pays
    assert option.unit_cost == 600         # what a tier column shows


def test_the_unit_cost_excludes_the_party_seat_fee():
    """`seat_fee_estimate` is a whole-party figure, so dividing it into a
    per-seat fare would invent a number no airline quotes."""
    option = _candidate_to_option(candidate(price=600, seat_fee=90), "SGD", "a", party_size=3)
    assert option.estimated_cost == 1890
    assert option.unit_cost == 600


def test_an_option_with_no_per_person_meaning_has_no_unit_cost():
    """A hotel room is not priced per traveller, so it must not claim to be."""
    assert Option(name="Shinjuku", description="d", category="hotel",
                  estimated_cost=1200).unit_cost is None


def test_the_recommendation_states_how_many_travellers_its_figures_cover():
    req = TravelRequest(
        origin="Singapore", destination="Japan", destination_city="Tokyo",
        departure_date="2026-10-10", return_date="2026-10-16", travellers=3,
        traveller_ages=[38, 36, 9], traveller_genders=["female", "male", "male"],
        traveller_accessibility_needs=[[], [], []], budget=9000, currency="SGD",
    )
    rec = recommend_package(req, [
        AgentFinding(agent="flight_agent", summary="s", confidence=0.9, options=[
            _candidate_to_option(candidate(price=600), "SGD", "a", party_size=3),
            _candidate_to_option(candidate(price=550, fid="JL4002-20261016",
                                           direction="RETURN"), "SGD", "a", party_size=3),
        ]),
        AgentFinding(agent="hotel_transport_agent", summary="s", confidence=0.9, options=[
            Option(category="hotel", name="Shinjuku", description="d",
                   estimated_cost=1200, currency="SGD"),
        ]),
    ])
    assert rec.travellers == 3
    assert rec.total == 1800 + 1650 + 1200


def test_banding_is_unaffected_by_which_figure_is_displayed():
    """Every fare scales by the same party size, so the cheapest flight is the
    cheapest whichever number the card prints."""
    from flaskapp.travel_ai.sections import band_by_price

    options = [
        _candidate_to_option(candidate(price=p, fid=f"X{p}"), "SGD", "a", party_size=4)
        for p in (900, 300, 600)
    ]
    tiers = band_by_price(options)
    assert [t.options[0].unit_cost for t in tiers] == [300, 600, 900]


# --- the renderer -----------------------------------------------------------
#
# No JS test harness in this repo, so these assert against the source. Weaker
# than running it, but they still fail if someone drops the distinction.

def _js():
    from pathlib import Path
    return Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")


def test_the_tier_columns_ask_for_the_per_traveller_figure():
    assert "optionCard(option, {perTraveller: true})" in _js()


def test_the_recommendation_passes_the_traveller_count():
    assert "optionCard(option, {travellers: rec.travellers || 1})" in _js()


def test_a_per_traveller_price_says_so():
    js = _js()
    assert 'suffix = " per traveller";' in js
    assert "suffix = ` (${opts.travellers} travellers)`;" in js


def test_the_card_falls_back_when_an_option_has_no_unit_cost():
    """A hotel in a tier column has no per-seat figure, so it must still show
    its own cost rather than nothing."""
    js = _js()
    assert "const perTraveller = opts.perTraveller && option.unit_cost !== null" in js
    assert "const shown = perTraveller ? option.unit_cost : option.estimated_cost;" in js
