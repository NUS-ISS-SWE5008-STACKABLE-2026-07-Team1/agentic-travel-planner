"""Transposing banded sections into tier-major columns.

`plan_sections` is section-major: a Flight section holding tiers. A package
column is the other way round — a Budget column holding a flight group and a
hotel group, so a traveller reads a trip rather than pairing two grids.
"""

from flaskapp.travel_ai.schemas import AgentFinding, Option
from flaskapp.travel_ai.sections import BANDED_SECTIONS, plan_packages, plan_sections


def finding(agent, options=(), summary="ran"):
    return AgentFinding(agent=agent, summary=summary, options=list(options), confidence=0.9)


def option(name, cost, category):
    return Option(category=category, name=name, description="d",
                  estimated_cost=cost, currency="SGD")


def labels(packages):
    return [package.label for package in packages]


def titles(package):
    return [group.title for group in package.groups]


FLIGHTS = finding("flight_agent", [
    option("f-cheap", 500, "flight"), option("f-mid", 900, "flight"),
    option("f-dear", 1800, "flight"),
])
HOTELS = finding("hotel_transport_agent", [
    option("h-cheap", 300, "hotel"), option("h-mid", 800, "hotel"),
    option("h-dear", 2400, "hotel"),
])


def test_a_column_holds_both_a_flight_and_a_hotel_group():
    packages = plan_packages([FLIGHTS, HOTELS])
    budget = next(p for p in packages if p.label == "Budget")
    assert titles(budget) == ["Flight details", "Hotel details"]
    assert [o.name for g in budget.groups for o in g.options] == ["f-cheap", "h-cheap"]


def test_columns_run_budget_to_luxury():
    assert labels(plan_packages([FLIGHTS, HOTELS])) == ["Budget", "Comfort", "Luxury"]


def test_each_group_carries_its_icon():
    packages = plan_packages([FLIGHTS, HOTELS])
    assert all(group.icon.strip() for package in packages for group in package.groups)


def test_banding_stays_independent_per_section():
    """Six flights split 2/2/2 while three hotels split 1/1/1."""
    six = finding("flight_agent", [
        option(f"f{i}", (i + 1) * 100, "flight") for i in range(6)
    ])
    packages = plan_packages([six, HOTELS])
    budget = next(p for p in packages if p.label == "Budget")
    by_title = {g.title: len(g.options) for g in budget.groups}
    assert by_title == {"Flight details": 2, "Hotel details": 1}


def test_a_hotel_only_plan_produces_hotel_only_columns():
    """Selective dispatch composes for free: there is no flight finding."""
    packages = plan_packages([HOTELS])
    assert all(titles(package) == ["Hotel details"] for package in packages)


def test_a_tier_with_no_groups_is_dropped():
    one = finding("flight_agent", [option("only", 100, "flight")])
    assert labels(plan_packages([one])) == ["Budget"]


def test_no_banded_findings_yields_no_packages():
    assert plan_packages([finding("risk_advisory_agent", [])]) == []


def test_banded_sections_no_longer_appear_in_plan_sections():
    """They live in packages now. Showing them in both would put the same
    options on screen twice and give two places to fix when one is wrong."""
    titles_out = [section.title for section in plan_sections([FLIGHTS, HOTELS])]
    assert not (set(titles_out) & BANDED_SECTIONS)


def test_unbanded_sections_still_come_through():
    sections = plan_sections([finding("risk_advisory_agent", [
        Option(name="visa", description="d"),
    ])])
    assert [s.title for s in sections] == ["Risk and advisory"]


def test_a_packaged_flight_carries_its_formatted_schedule():
    """Formatting is server-side, so the renderer never parses a timestamp.

    Applied to a COPY: `agent_findings` must keep the raw options exactly as the
    specialist returned them.
    """
    from flaskapp.travel_ai.schemas import OptionSchedule

    raw = Option(
        category="flight", name="JL6006 (Outbound)", description="d",
        estimated_cost=900.0, currency="SGD",
        schedule=OptionSchedule(
            reference="JL6006", depart="2026-10-10T17:15+08:00",
            arrive="2026-10-11T01:06+09:00", dest_code="HND",
        ),
    )
    flights = finding("flight_agent", [raw])
    packaged = plan_packages([flights])[0].groups[0].options[0]
    assert packaged.schedule_display["date"] == "10 Oct 2026"
    assert packaged.schedule_display["depart"] == "17:15"
    assert packaged.schedule_display["day_offset"] == "+1"
    assert raw.schedule_display is None, "the specialist's own option is untouched"


def test_an_option_without_a_schedule_has_no_display():
    packaged = plan_packages([finding("flight_agent", [
        option("prose only", 500, "flight"),
    ])])[0].groups[0].options[0]
    assert packaged.schedule_display is None
