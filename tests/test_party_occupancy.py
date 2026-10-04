"""How many rooms a party needs, and how a transfer's price scales with it.

Both are occupancy questions the costs used to ignore: a hotel was priced as
one room however many people travelled, and a transfer cost the same for one
traveller as for six.

The room split is at 12 and is NOT the existing `CHILD_AGE_LIMIT = 18`. That
constant derives `party` composition — who counts as a child for the trip —
and reusing it here would put a 15-year-old in the children's allowance
instead of the adult one, quietly under-booking rooms for a family.
"""

import pytest

from flaskapp.travel_ai.agents.hotel_transport_agent.domain import (
    rooms_required, transport_units,
)


# --- rooms: 2 over-12s + 2 children of 12 or under, per room -----------------

@pytest.mark.parametrize("ages,rooms", [
    ([34], 1),                       # one traveller still needs a room
    ([34, 31], 1),                   # two adults share
    ([34, 31, 8], 1),                # + a child, still one room
    ([34, 31, 8, 6], 1),             # the stated maximum: 2 + 2
    ([34, 31, 8, 6, 4], 2),          # a third child spills over
    ([34, 31, 29], 2),               # three adults cannot share one room
    ([34, 31, 29, 27], 2),           # four adults, two rooms
    ([34, 31, 29, 27, 25], 3),       # five adults, three rooms
])
def test_rooms_required(ages, rooms):
    assert rooms_required(ages) == rooms


def test_thirteen_is_an_adult_and_twelve_is_a_child():
    """The boundary, stated explicitly because the rule is 'greater than 12'.

    Exactly 12 is unassigned by that wording, so it goes with the children —
    which makes the split total, so nobody is dropped or counted twice.
    """
    assert rooms_required([34, 13]) == 1      # two adults
    assert rooms_required([34, 13, 31]) == 2  # three adults
    assert rooms_required([34, 31, 12, 12]) == 1   # 12 counts as a child
    assert rooms_required([34, 31, 12, 12, 12]) == 2


def test_no_ages_still_books_a_room():
    assert rooms_required([]) == 1


# --- transport: a car is per vehicle, a ticket is per person -----------------

@pytest.mark.parametrize("mode,travellers,units", [
    ("taxi", 1, 1), ("taxi", 4, 1), ("taxi", 5, 2), ("taxi", 8, 2), ("taxi", 9, 3),
    ("train", 1, 1), ("train", 3, 3),
    ("bus", 3, 3),
])
def test_transport_units(mode, travellers, units):
    assert transport_units(mode, travellers) == units


def test_an_unknown_mode_is_priced_per_person():
    """The safer default: over-stating a shared vehicle is a smaller error than
    quoting one ferry ticket to a family of five."""
    assert transport_units("ferry", 5) == 5


def test_mode_matching_is_case_insensitive():
    assert transport_units("Taxi", 5) == 2


# --- the costs that actually reach the traveller ----------------------------

def _hotel_request(ages, check_in="2026-10-10", check_out="2026-10-12"):
    from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import (
        HotelProposalRequest, HotelTripContext,
    )
    return HotelProposalRequest(trip_context=HotelTripContext(
        dest_city_slug="jp-tokyo", check_in_date=check_in, check_out_date=check_out,
        traveller_ages=list(ages),
    ))


def _inventory():
    from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import HotelInventoryItem
    return [HotelInventoryItem(
        hotel_id="h1", name="Shinjuku Grand", city_slug="jp-tokyo",
        price_per_night=100.0, room_type="double",
    )]


def test_the_hotel_total_multiplies_by_rooms_not_just_nights():
    """Two nights at 100 is 200 for a couple, and 400 once they need two rooms."""
    from flaskapp.travel_ai.agents.hotel_transport_agent.domain import propose_hotels

    couple = propose_hotels(_hotel_request([34, 31]), _inventory()).candidates[0]
    assert couple.estimated_total_cost == 200.0

    five_adults = propose_hotels(_hotel_request([34, 31, 29, 27, 25]), _inventory()).candidates[0]
    assert five_adults.estimated_total_cost == 600.0   # 3 rooms x 2 nights x 100


def test_a_family_of_four_still_books_one_room():
    from flaskapp.travel_ai.agents.hotel_transport_agent.domain import propose_hotels

    family = propose_hotels(_hotel_request([38, 36, 9, 7]), _inventory()).candidates[0]
    assert family.estimated_total_cost == 200.0


def test_a_taxi_is_billed_per_car_and_a_train_per_traveller():
    from flaskapp.travel_ai.agents.hotel_transport_agent.agent import _transport_to_option
    from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import TransportOption

    def opt(mode, cost=40.0):
        return TransportOption(name=f"{mode} transfer", mode=mode,
                               airport="NRT", estimated_cost=cost, currency="SGD")

    assert _transport_to_option(opt("taxi"), "SGD", travellers=4).estimated_cost == 40.0
    assert _transport_to_option(opt("taxi"), "SGD", travellers=5).estimated_cost == 80.0
    assert _transport_to_option(opt("train", 12.0), "SGD", travellers=5).estimated_cost == 60.0
    assert _transport_to_option(opt("bus", 10.0), "SGD", travellers=3).estimated_cost == 30.0
