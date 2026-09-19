"""Transport for every airport the destination city has.

Never fetched in production: `to_hotel_request` derived the arrival airport from
the flight agent's options in graph state, but both agents start from START
together, so those findings are empty when the hotel node runs. Every grounded
run recorded transport_option_count: 0 while 46 seed options sat unused.
"""

from flaskapp.travel_ai.agents.hotel_transport_agent.agent import transport_options_for_city


def names(options):
    return [option.name for option in options]


def test_a_multi_airport_city_yields_transport_for_each():
    """jp-tokyo has both NRT and HND, and the seed holds a pair for each.

    One flight finding spans several arrival airports, so fetching for only one
    would pair a Haneda arrival with the Narita Express.
    """
    options = transport_options_for_city("Japan", "Tokyo", "SGD")
    airports = {option.airport for option in options}
    assert airports == {"NRT", "HND"}


def test_every_transport_option_is_tagged_and_categorised():
    options = transport_options_for_city("Japan", "Tokyo", "SGD")
    assert options, "the seed holds transport for this city"
    assert all(option.category == "transport" for option in options)
    assert all(option.airport for option in options)


def test_a_city_with_no_seed_transport_yields_nothing():
    """A real data gap, distinct from the wiring bug that produced the same zero."""
    assert transport_options_for_city("Japan", "Nowhere", "SGD") == []


def test_an_unresolvable_country_does_not_raise():
    assert transport_options_for_city("Atlantis", "Atlantis", "SGD") == []


def test_the_airport_survives_ranking():
    """`run_hotel_agent` reorders these, and the shared Option is built from the
    reordered list — so the tag has to live on the domain object itself."""
    from flaskapp.travel_ai.agents.hotel_transport_agent.agent import (
        _transport_to_option, transport_for_city,
    )

    transports = transport_for_city("Japan", "Tokyo")
    assert transports, "the seed holds transport for this city"
    assert all(t.airport for t in transports)
    for transport in reversed(transports):
        assert _transport_to_option(transport, "SGD").airport == transport.airport
