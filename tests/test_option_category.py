"""Each builder records what kind of option it produced.

Without this the shared `Option` contract loses the distinction: a hotel and an
airport transfer arrive in the same `finding.options` list, differing only by
prose inside `description`. Rendering them as separate sections by parsing that
prose would depend on a format nothing enforces.
"""

from flaskapp.travel_ai.schemas import Option


def test_an_option_without_a_category_is_still_valid():
    """Additive: every stored row and A2A artifact predates the field."""
    assert Option(name="n", description="d").category is None


def test_the_hotel_builder_says_hotel():
    from flaskapp.travel_ai.agents.hotel_transport_agent.agent import _hotel_candidate_to_option
    from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import HotelCandidate

    candidate = HotelCandidate(
        hotel_id="h1", name="Test Hotel", city_slug="jp-tokyo", room_type="double",
        price_per_night=150.0, distance_to_center_km=1.0, star_rating=4,
    )
    assert _hotel_candidate_to_option(candidate, "SGD", "seed data").category == "hotel"


def test_the_transport_builder_says_transport():
    from flaskapp.travel_ai.agents.hotel_transport_agent.agent import _transport_to_option
    from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import TransportOption

    option = TransportOption(
        name="Airport Express", mode="train",
        duration_minutes=45, distance_km=60.0, estimated_cost=25.0,
    )
    assert _transport_to_option(option, "SGD").category == "transport"
