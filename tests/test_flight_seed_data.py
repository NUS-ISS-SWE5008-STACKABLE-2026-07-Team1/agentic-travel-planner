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
