"""Which specialists a request dispatches. Pure: no model, no graph, no IO."""

import pytest

from flaskapp.travel_ai.dispatch import specialists_for
from flaskapp.travel_ai.schemas import TravelRequest

BASE = {
    "origin": "Singapore", "destination": "Japan", "destination_city": "Tokyo",
    "departure_date": "2026-10-10", "return_date": "2026-10-16",
    "travellers": 1, "traveller_ages": [34], "traveller_genders": ["male"],
    "traveller_accessibility_needs": [[]], "budget": 3000,
}


def request(**overrides):
    return TravelRequest.model_validate({**BASE, **overrides})


def test_an_absent_scope_dispatches_every_specialist():
    """Every stored row, golden scenario and pre-existing caller omits the field."""
    assert specialists_for(request()) == (
        "flight_agent", "hotel_transport_agent",
        "accessibility_agent", "risk_advisory_agent",
    )


def test_hotel_only_skips_the_flight_agent():
    assert "flight_agent" not in specialists_for(request(plan_scope="hotel"))


def test_flights_only_skips_the_hotel_agent():
    assert "hotel_transport_agent" not in specialists_for(request(plan_scope="flights"))


@pytest.mark.parametrize("scope", ["both", "flights"])
def test_risk_advisory_runs_for_any_scope_that_involves_travel(scope):
    """Visa and entry rules are not opt-out for a journey.

    Hotel-only is the one exception, and deliberately so — see
    `test_hotel_only_dispatches_the_hotel_agent_alone`.
    """
    assert "risk_advisory_agent" in specialists_for(request(plan_scope=scope))


def test_stated_accessibility_needs_override_a_hotel_only_scope():
    """`assess_plan` warns when needs exist and no accessibility finding does.

    Skipping the agent would ship a plan that ignores a stated requirement and
    then complains about itself.
    """
    selected = specialists_for(
        request(plan_scope="hotel", accessibility_needs=["step-free access"])
    )
    assert "accessibility_agent" in selected
    assert "flight_agent" not in selected


def test_per_traveller_needs_also_pull_in_the_accessibility_agent():
    selected = specialists_for(request(
        plan_scope="flights", traveller_accessibility_needs=[["wheelchair"]],
    ))
    assert "accessibility_agent" in selected


def test_no_stated_needs_and_a_narrow_scope_drops_accessibility():
    assert "accessibility_agent" not in specialists_for(request(plan_scope="hotel"))


def test_the_result_is_always_ordered_and_never_empty():
    """Order is SPECIALISTS order so traces stay comparable between runs."""
    from flaskapp.travel_ai.graph import SPECIALISTS
    for scope in ("both", "flights", "hotel"):
        selected = specialists_for(request(plan_scope=scope))
        assert selected, "risk advisory guarantees at least one source for the barrier"
        assert list(selected) == [n for n in SPECIALISTS if n in selected]


# --- hotel-only is a stay, not a trip ----------------------------------------


def test_hotel_only_dispatches_the_hotel_agent_alone():
    """Reversal of "advisory is not opt-out", made deliberately.

    Risk & Advisory surfaces visa and entry rules, which are reasoned from the
    departure country. A hotel-only request no longer collects one, so the
    agent would be advising on a journey it knows nothing about.
    """
    assert specialists_for(request(plan_scope="hotel", origin=None)) == (
        "hotel_transport_agent",
    )


def test_risk_advisory_still_runs_for_every_other_scope():
    for scope in ("both", "flights"):
        assert "risk_advisory_agent" in specialists_for(request(plan_scope=scope))


def test_hotel_only_still_gets_accessibility_when_needs_are_stated():
    selected = specialists_for(request(
        plan_scope="hotel", origin=None, accessibility_needs=["step-free access"],
    ))
    assert selected == ("hotel_transport_agent", "accessibility_agent")


def test_a_hotel_only_request_is_valid_without_an_origin():
    assert request(plan_scope="hotel", origin=None).origin is None


def test_every_other_scope_still_requires_an_origin():
    for scope in ("both", "flights"):
        with pytest.raises(ValueError, match="origin"):
            request(plan_scope=scope, origin=None)
