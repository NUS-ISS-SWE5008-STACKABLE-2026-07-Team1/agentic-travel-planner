"""End-to-end dispatch through the planning service, with the model stubbed.

The graph and the A2A message list must come from ONE `specialists_for` call.
`make_specialist_node` raises `Missing A2A request for {name}` when a node runs
without one, so a graph and a message list that disagree is a crash, not a
degraded plan.
"""

import pytest

from flaskapp.travel_ai.schemas import AgentFinding, TravelPlan, TravelRequest

BASE = {
    "origin": "Singapore", "destination": "Japan", "destination_city": "Tokyo",
    "departure_date": "2026-10-10", "return_date": "2026-10-16",
    "travellers": 1, "traveller_ages": [34], "traveller_genders": ["male"],
    "traveller_accessibility_needs": [[]], "budget": 3000,
}


@pytest.fixture
def planned(monkeypatch, tmp_path):
    """Run the service with every specialist replaced by a recording stub."""
    called: list[str] = []

    def fake_factory(name):
        def create_node(*_args, **_kwargs):
            def node(state):
                called.append(name)
                finding = AgentFinding(agent=name, summary=f"{name} ran", confidence=0.9)
                return {"findings": [finding], "messages": []}
            return node
        return create_node

    import flaskapp.travel_ai.graph as graph_module
    monkeypatch.setattr(graph_module, "SPECIALIST_NODE_FACTORIES", {
        name: fake_factory(name) for name in graph_module.SPECIALISTS
    })
    monkeypatch.setattr(
        graph_module, "_specialist_node",
        lambda name, create_node, llm, tracer, settings: create_node(),
    )

    def fake_orchestrator(_llm, _tracer, _guardrail=None):
        def node(state):
            return {"plan": TravelPlan(
                title="t", summary="s", itinerary=[], rationale=[], limitations=[],
            )}
        return node

    monkeypatch.setattr(graph_module, "create_orchestrator_node", fake_orchestrator)

    def run(**overrides):
        called.clear()
        from flaskapp.travel_ai.service import TravelPlanningService
        service = TravelPlanningService(
            provider="openai", api_key="unused", model="stub",
            temperature=0, timeout=30, trace_dir=tmp_path, database_path=None,
        )
        monkeypatch.setattr(
            "flaskapp.travel_ai.service.build_llm", lambda **_kwargs: object()
        )
        response = service.create_plan(TravelRequest.model_validate({**BASE, **overrides}))
        return response, list(called)

    return run


def test_a_hotel_only_request_runs_the_hotel_agent_and_the_advisory(planned):
    """A stay, not a journey — but still somewhere with laws and entry rules.

    Asserted through the real graph rather than `specialists_for` alone: the
    advisory node has to survive a request whose `origin` is None, not merely
    appear in the selected tuple.
    """
    _response, called = planned(plan_scope="hotel", origin=None)
    assert called == ["hotel_transport_agent", "risk_advisory_agent"]


def test_a_default_request_still_runs_every_specialist(planned):
    _response, called = planned()
    assert sorted(called) == sorted([
        "flight_agent", "hotel_transport_agent",
        "accessibility_agent", "risk_advisory_agent",
    ])
    assert called[-1] == "accessibility_agent"


def test_the_plan_states_which_specialists_were_skipped(planned):
    response, _called = planned(plan_scope="hotel")
    limitations = " ".join(response.plan.limitations)
    assert "flight_agent" in limitations
    assert "hotel_transport_agent" not in limitations
    # An agent that ran is not a limitation. Risk & Advisory now runs at every
    # scope, so naming it here would report a gap the plan does not have.
    assert "risk_advisory_agent" not in limitations


def test_the_response_carries_ordered_sections(planned):
    """Derived at response time, never stored: the findings are already saved
    and a second copy could disagree with the first."""
    response, _called = planned()
    titles = [section.title for section in response.sections]
    assert titles[-1].startswith("Risk"), "advisory qualifies the plan above it"
    # Flight and hotel are tier columns now, not sections.
    assert not any("Flight" in title for title in titles)


def test_a_hotel_only_response_has_no_flight_group(planned):
    """Selective dispatch composes with the transpose: no flight finding means
    no flight group, with no scope check in the renderer."""
    response, _called = planned(plan_scope="hotel", origin=None)
    titles = [group.title for package in response.packages for group in package.groups]
    assert not any("Flight" in title for title in titles)


def test_the_response_carries_icons_and_tiers(planned):
    """A section with no options has no tiers — there is nothing to band, and
    the summary is what it has to say. Icons are unconditional."""
    response, _called = planned()
    for section in response.sections:
        assert section.icon.strip(), section.title
        if section.options:
            assert section.tiers, f"{section.title} has options but no tiers"
        else:
            assert section.tiers == [], f"{section.title} has tiers but no options"


def test_the_response_always_carries_a_recommendation(planned):
    """Present even when nothing fits, so the traveller learns the trip does not
    fit rather than seeing nothing at all."""
    response, _called = planned()
    assert response.recommendation is not None
    assert response.recommendation.items or response.recommendation.note
