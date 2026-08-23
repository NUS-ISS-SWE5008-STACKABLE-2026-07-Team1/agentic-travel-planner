from collections import Counter

from flaskapp.travel_ai.agents.flight_agent.seed_data import GOLDEN_SCENARIO_FLIGHTS, SEED_FLIGHT_INVENTORY


def test_no_duplicate_flight_ids():
    ids = [item.flight_id for item in SEED_FLIGHT_INVENTORY]
    dupes = [flight_id for flight_id, count in Counter(ids).items() if count > 1]
    assert dupes == []


def test_dataset_is_large_enough_to_be_meaningful():
    assert len(SEED_FLIGHT_INVENTORY) >= 100


def test_golden_scenario_flights_are_included_unmodified():
    golden_ids = {item.flight_id for item in GOLDEN_SCENARIO_FLIGHTS}
    seed_by_id = {item.flight_id: item for item in SEED_FLIGHT_INVENTORY}
    for golden in GOLDEN_SCENARIO_FLIGHTS:
        assert seed_by_id[golden.flight_id] == golden
    assert golden_ids <= {item.flight_id for item in SEED_FLIGHT_INVENTORY}


def test_multiple_routes_present():
    routes = {item.route for item in SEED_FLIGHT_INVENTORY}
    assert routes >= {"SIN#NRT", "NRT#SIN", "SIN#LHR", "SIN#SYD", "SIN#BKK", "SIN#HKG"}


def test_extended_rows_carry_seat_inventory_golden_rows_do_not():
    golden_ids = {item.flight_id for item in GOLDEN_SCENARIO_FLIGHTS}
    for item in SEED_FLIGHT_INVENTORY:
        if item.flight_id in golden_ids:
            assert item.seat_inventory is None
        else:
            assert item.seat_inventory is not None


def test_dataset_has_two_hubs():
    """Shape, not size, is what limited this dataset.

    Every row used to have Singapore at one end, so any trip that did not touch
    Singapore had no data at all and fell to the unbacked prompt-only path. A
    second hub is the qualitative change; more rows of the same shape would not
    have been.
    """
    pairs = {(item.origin_airport, item.dest_airport) for item in SEED_FLIGHT_INVENTORY}
    without_singapore = {pair for pair in pairs if "SIN" not in pair}

    assert without_singapore, "every route still touches Singapore"
    assert {"LHR", "LGW"} & {airport for pair in without_singapore for airport in pair}


def test_some_routes_are_deliberately_unstocked():
    """The prompt-only path must stay reachable.

    If the dataset ever covered everything, the regression tests guarding the
    no-inventory path (`test_flight_path2_screening.py`) would silently stop
    exercising it — they would be testing the grounded path instead.
    """
    from flaskapp.travel_ai.agents.flight_agent.seed_data import covers_route

    assert not covers_route(("CDG",), ("FCO",)), "Paris-Rome is stocked; Path 2 tests need it not to be"
    assert not covers_route(("GRU",), ("SIN",)), "Brazil is stocked; Path 2 tests use it"


def test_golden_scenario_routes_are_unchanged_by_the_second_hub():
    """The London rows must not be able to enter a Singapore search.

    `domain._matches_route` filters by route before ranking, so this holds by
    construction — but it is the property that lets the seed dataset grow without
    rewriting `tests/golden/flight_scenarios.json`, so it is worth asserting.
    """
    sin_nrt = {
        item.flight_id for item in SEED_FLIGHT_INVENTORY
        if item.origin_airport == "SIN" and item.dest_airport == "NRT"
    }
    london_origin = {
        item.flight_id for item in SEED_FLIGHT_INVENTORY if item.origin_airport == "LHR"
    }

    assert london_origin, "the London hub is missing"
    assert not (sin_nrt & london_origin)
