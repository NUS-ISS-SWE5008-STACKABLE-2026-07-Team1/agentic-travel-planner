from flaskapp.travel_ai.agents.accessibility_agent.planning import (
    build_search_plan, extract_requirements,
)


def test_extracts_typed_per_traveller_requirements_without_duplicates():
    state = {"request": {
        "traveller_accessibility_needs": [
            ["powered wheelchair", "service animal"], ["induction loop"],
        ],
        "accessibility_needs": ["Traveler 1: powered wheelchair"],
    }}
    requirements = extract_requirements(state)

    assert [item.category for item in requirements] == [
        "mobility", "service_animal", "hearing",
    ]
    assert [item.traveller_index for item in requirements] == [0, 0, 1]


def test_search_plan_uses_city_and_category_but_not_raw_medical_detail():
    private_detail = "CPAP serial PRIVATE-12345"
    plan = build_search_plan({"request": {
        "destination": "Japan", "destination_city": "Tokyo",
        "traveller_accessibility_needs": [[private_detail]],
    }})

    assert plan.destination == "Tokyo, Japan"
    assert plan.requirements[0].category == "medical_equipment"
    assert private_detail not in " ".join(plan.queries)
    assert "medical equipment policy" in plan.queries[0]
    assert len(plan.journey_segments) == 5


def test_no_stated_need_still_builds_a_general_accessibility_query():
    plan = build_search_plan({"request": {"destination": "France"}})
    assert plan.requirements == []
    assert "accessibility services" in plan.queries[0]
