"""A flight Option's cost is what the PARTY pays, not what one seat costs.

`domain._effective_cost` — the number ranking sorts on — has always been
`price * party_size + seat_fee_estimate`. The Option handed to the traveller
was built from `price + seat_fee_estimate`, which is not a smaller version of
the same figure: it adds a PER-SEAT fare to a WHOLE-PARTY seat fee, so for a
party of three it was neither one fare nor three.

That figure is what the tier columns print and what `recommend_package` sums
into "closest to your budget", so a family of three was told a trip cost a
third of its real price.
"""

from flaskapp.travel_ai.agents.flight_agent.agent import _candidate_to_option
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightCandidate


def candidate(price=600.0, seat_fee=0.0):
    return FlightCandidate(
        flight_id="JL4001-20261010", direction="OUTBOUND",
        dep_ts="2026-10-10T09:00+08:00", arr_ts="2026-10-10T17:00+09:00",
        dest_airport="NRT", stops=0, price=price, seats=9,
        wheelchair_assist_available=None, step_free_boarding=None,
        seat_fee_estimate=seat_fee,
    )


def test_one_traveller_pays_one_fare():
    option = _candidate_to_option(candidate(price=600), "SGD", "a", party_size=1)
    assert option.estimated_cost == 600


def test_three_travellers_pay_three_fares():
    option = _candidate_to_option(candidate(price=600), "SGD", "a", party_size=3)
    assert option.estimated_cost == 1800


def test_the_seat_fee_is_added_once_because_it_is_already_a_party_total():
    """`seat_fee_estimate` is documented as the fee for the WHOLE party, so
    scaling it too would charge a party of three nine times over."""
    option = _candidate_to_option(candidate(price=600, seat_fee=90), "SGD", "a", party_size=3)
    assert option.estimated_cost == 1890


def test_it_matches_what_ranking_sorted_on():
    """The displayed cost and the ranked cost must be the same number.

    If they drift, the cheapest option by the ranking is not the cheapest
    option on screen, and nothing in the UI would reveal it.
    """
    from flaskapp.travel_ai.agents.flight_agent.domain import _effective_cost
    from flaskapp.travel_ai.agents.flight_agent.schemas import (
        FlightInventoryItem, FlightPreferences,
    )

    item = FlightInventoryItem(
        flight_id="JL4001-20261010", carrier="JL", flight_no="4001",
        origin_airport="SIN", dest_airport="NRT",
        dep_ts="2026-10-10T09:00+08:00", arr_ts="2026-10-10T17:00+09:00",
        duration_min=420, price=600.0, cabin_class="ECONOMY", seats_available=9,
        stops=0, wheelchair_assist_available=None, step_free_boarding=None,
    )
    ranked = _effective_cost(item, FlightPreferences(), party_size=3)
    shown = _candidate_to_option(candidate(price=600), "SGD", "a", party_size=3).estimated_cost
    assert shown == ranked
