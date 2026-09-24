"""Choosing one combination that fits the traveller's stated budget.

Pure: a request and findings in, a recommendation out. Exhaustive rather than
greedy, because a dearer flight paired with a much cheaper hotel can land closer
to budget than the cheapest of each.
"""

from flaskapp.travel_ai.recommendation import recommend_package
from flaskapp.travel_ai.schemas import AgentFinding, Option, OptionSchedule, TravelRequest

BASE = {
    "origin": "Singapore", "destination": "Japan", "destination_city": "Tokyo",
    "departure_date": "2026-10-10", "return_date": "2026-10-16",
    "travellers": 1, "traveller_ages": [34], "traveller_genders": ["male"],
    "traveller_accessibility_needs": [[]], "currency": "SGD",
}


def request(budget=5000, **overrides):
    return TravelRequest.model_validate({**BASE, "budget": budget, **overrides})


def flight(name, cost, direction, airport="NRT", currency="SGD"):
    return Option(
        category="flight", name=name, description="d", airport=airport,
        estimated_cost=cost, currency=currency,
        schedule=OptionSchedule(reference=name, depart="2026-10-10T09:00+08:00",
                                arrive="2026-10-10T17:00+09:00",
                                dest_code=airport, direction=direction),
    )


def hotel(name, cost, currency="SGD"):
    return Option(category="hotel", name=name, description="d",
                  estimated_cost=cost, currency=currency)


def transfer(name, cost, airport="NRT"):
    return Option(category="transport", name=name, description="d",
                  airport=airport, estimated_cost=cost, currency="SGD")


def findings(flights=(), hotels=(), transfers=()):
    out = []
    if flights:
        out.append(AgentFinding(agent="flight_agent", summary="s",
                                options=list(flights), confidence=0.9))
    if hotels or transfers:
        out.append(AgentFinding(agent="hotel_transport_agent", summary="s",
                                options=[*hotels, *transfers], confidence=0.9))
    return out


def names(rec):
    return [item.name for item in rec.items]


def test_recommended_flights_carry_the_formatted_schedule():
    """The recommendation renders through the same `optionCard` as a tier
    column, so it needs the same formatted schedule.

    Without it the card falls back to the raw option name — "JL4001-20261010
    (Outbound)" — which shows an unformatted date, in the one panel a traveller
    reads first.
    """
    rec = recommend_package(request(budget=3000), findings(
        flights=[flight("JL4001-20261010", 800, "OUTBOUND"),
                 flight("JL4002-20261016", 900, "RETURN")],
        hotels=[hotel("Shinjuku", 1000)],
    ))

    flights = [item for item in rec.items if item.category == "flight"]
    assert flights, "a recommendation with flights in it"
    for item in flights:
        assert item.schedule_display, f"{item.name} reached the card unformatted"
        assert item.schedule_display["date"] == "10 Oct 2026"
    # The embedded -YYYYMMDD is stripped from the reference, because the card
    # shows that date on its own line.
    assert {item.schedule_display["reference"] for item in flights} == {"JL4001", "JL4002"}


def test_a_hotel_without_a_schedule_is_left_alone():
    """Only flights carry one; formatting must not invent a display for a
    hotel, which would render an empty date line on its card."""
    rec = recommend_package(request(budget=3000), findings(
        flights=[flight("out", 800, "OUTBOUND"), flight("back", 900, "RETURN")],
        hotels=[hotel("Shinjuku", 1000)],
    ))

    hotels = [item for item in rec.items if item.category == "hotel"]
    assert hotels and all(item.schedule_display is None for item in hotels)


def test_it_picks_the_combination_closest_under_budget():
    rec = recommend_package(request(budget=3000), findings(
        flights=[flight("out-cheap", 800, "OUTBOUND"), flight("out-dear", 1200, "OUTBOUND"),
                 flight("back", 700, "RETURN")],
        hotels=[hotel("h-cheap", 400), hotel("h-dear", 1000)],
    ))
    # 1200 + 700 + 1000 = 2900 beats 800 + 700 + 1000 = 2500.
    assert rec.total == 2900
    assert set(names(rec)) == {"out-dear", "back", "h-dear"}
    assert rec.remaining == 100


def test_a_dearer_flight_with_a_cheaper_hotel_can_win():
    """The case a greedy cheapest-first pick would miss."""
    rec = recommend_package(request(budget=2000), findings(
        flights=[flight("out-a", 500, "OUTBOUND"), flight("out-b", 900, "OUTBOUND"),
                 flight("back", 400, "RETURN")],
        hotels=[hotel("h-big", 1000), hotel("h-small", 600)],
    ))
    # 900 + 400 + 600 = 1900 beats 500 + 400 + 1000 = 1900? equal — prefer either,
    # but 900+400+600 and 500+400+1000 both equal 1900; the point is neither
    # exceeds and the total is maximal.
    assert rec.total == 1900


def test_transport_must_serve_the_chosen_flights_airport():
    """Otherwise the plan says fly to Haneda and take the Narita Express."""
    rec = recommend_package(request(budget=3000), findings(
        flights=[flight("to-hnd", 900, "OUTBOUND", airport="HND"),
                 flight("back", 700, "RETURN", airport="SIN")],
        hotels=[hotel("h", 500)],
        transfers=[transfer("narita-express", 50, airport="NRT"),
                   transfer("haneda-monorail", 40, airport="HND")],
    ))
    assert "haneda-monorail" in names(rec)
    assert "narita-express" not in names(rec)


def test_nothing_fitting_is_reported_with_the_shortfall():
    """A planner that never books must not propose a trip nobody can afford."""
    rec = recommend_package(request(budget=500), findings(
        flights=[flight("out", 800, "OUTBOUND"), flight("back", 700, "RETURN")],
        hotels=[hotel("h", 400)],
    ))
    assert rec.items == []
    assert "1900" in rec.note or "1,900" in rec.note
    assert rec.total == 0


def test_a_missing_return_leg_yields_no_recommendation():
    """A package without a return flight is not a trip."""
    rec = recommend_package(request(), findings(
        flights=[flight("out", 800, "OUTBOUND")], hotels=[hotel("h", 400)],
    ))
    assert rec.items == []
    assert rec.note


def test_a_missing_hotel_section_yields_no_recommendation():
    rec = recommend_package(request(), findings(
        flights=[flight("out", 800, "OUTBOUND"), flight("back", 700, "RETURN")],
    ))
    assert rec.items == []


def test_options_in_another_currency_are_excluded():
    """Summing 100 USD with 500 SGD would be a wrong answer that looks right."""
    rec = recommend_package(request(budget=3000), findings(
        flights=[flight("out", 800, "OUTBOUND"), flight("out-usd", 900, "OUTBOUND", currency="USD"),
                 flight("back", 700, "RETURN")],
        hotels=[hotel("h", 400)],
    ))
    assert "out-usd" not in names(rec)


def test_a_city_with_no_transport_still_recommends_flights_and_hotel():
    rec = recommend_package(request(budget=3000), findings(
        flights=[flight("out", 800, "OUTBOUND"), flight("back", 700, "RETURN")],
        hotels=[hotel("h", 400)],
    ))
    assert len(rec.items) == 3
    assert rec.total == 1900


def test_no_budget_yields_no_recommendation():
    rec = recommend_package(request(budget=0.01), findings(
        flights=[flight("out", 800, "OUTBOUND"), flight("back", 700, "RETURN")],
        hotels=[hotel("h", 400)],
    ))
    assert rec.items == []


def test_the_card_renders_above_the_tier_grid():
    """It answers "can I afford this", which comes before comparing options."""
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    card = javascript.index('box.className = "plan-recommendation')
    grid = javascript.index('grid.className = "plan-tier-grid"')
    assert card < grid
    css = Path("flaskapp/static/css/app.css").read_text(encoding="utf-8")
    assert ".plan-recommendation" in css


def test_a_transfer_that_exists_but_does_not_fit_is_reported_honestly():
    """Observed live: 4,982 + a 35 transfer exceeded a 5,000 budget, so the
    search correctly dropped it — and then claimed none was available."""
    rec = recommend_package(request(budget=1000), findings(
        flights=[flight("out", 400, "OUTBOUND"), flight("back", 300, "RETURN")],
        hotels=[hotel("h", 290)],
        transfers=[transfer("bus", 50)],
    ))
    assert rec.total == 990, "the transfer does not fit inside the budget"
    # It must say the transfer did not FIT, not that none existed.
    assert "did not fit" in rec.note.lower(), rec.note
    assert not rec.note.lower().startswith("no verified"), rec.note


def test_no_transfer_existing_at_all_says_so():
    rec = recommend_package(request(budget=3000), findings(
        flights=[flight("out", 800, "OUTBOUND"), flight("back", 700, "RETURN")],
        hotels=[hotel("h", 400)],
    ))
    assert "available" in rec.note.lower()
