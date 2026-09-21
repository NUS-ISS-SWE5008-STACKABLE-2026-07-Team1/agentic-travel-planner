"""FLIGHT_AGENT_TRANSPORT has to survive the trip from config to graph.

The flag, its config entry, `graph._specialist_node`'s branch on it and the
ADR describing it all existed while `TravelPlanningService` quietly dropped it
from the settings it hands the graph — so setting it in the environment did
nothing on the website path, and only `tests/test_a2a_client_node.py`, which
calls the graph directly, could tell. These tests cover the plumbing between
those two points instead of the branch itself.
"""

from __future__ import annotations

import pytest

from flaskapp.travel_ai.service import TravelPlanningService

ENDPOINT = "http://flight-agent:8000/a2a/flight_agent"


@pytest.fixture
def captured_graph_config(monkeypatch, tmp_path):
    """The settings dict `create_plan` actually passes to `build_travel_graph`."""
    seen: dict = {}

    def fake_build_travel_graph(_llm, _tracer, _cancel=None, _guardrail=None,
                                config=None, specialists=()):
        seen.update(config or {})
        raise _Stop

    monkeypatch.setattr("flaskapp.travel_ai.service.build_travel_graph", fake_build_travel_graph)
    monkeypatch.setattr("flaskapp.travel_ai.service.build_llm", lambda **_kwargs: object())

    def run(**service_kwargs) -> dict:
        from flaskapp.travel_ai.schemas import TravelRequest

        service = TravelPlanningService(
            provider="openai", api_key="unused", model="stub", temperature=0,
            timeout=30, trace_dir=tmp_path, database_path=None, **service_kwargs,
        )
        request = TravelRequest.model_validate({
            "origin": "Singapore", "destination": "Japan",
            "departure_date": "2026-10-10", "return_date": "2026-10-16",
            "travellers": 1, "traveller_ages": [34], "traveller_genders": ["male"],
            "traveller_accessibility_needs": [[]], "budget": 3000,
        })
        seen.clear()
        with pytest.raises(_Stop):
            service.create_plan(request)
        return dict(seen)

    return run


class _Stop(Exception):
    """Ends create_plan once the graph settings have been captured."""


def test_the_transport_setting_reaches_the_graph(captured_graph_config):
    config = captured_graph_config(
        flight_agent_transport="a2a", flight_agent_a2a_url=ENDPOINT,
    )
    assert config["FLIGHT_AGENT_TRANSPORT"] == "a2a"
    assert config["FLIGHT_AGENT_A2A_URL"] == ENDPOINT


def test_the_default_is_still_in_process(captured_graph_config):
    config = captured_graph_config()
    assert config["FLIGHT_AGENT_TRANSPORT"] == "inprocess"
    assert config["FLIGHT_AGENT_A2A_URL"] == ""


def test_the_flight_seam_is_independent_of_the_all_specialists_switch(captured_graph_config):
    """Two different decisions: A2A_INTERNAL_ENABLED routes every specialist
    over A2A, this one routes only the flight agent."""
    config = captured_graph_config(flight_agent_transport="a2a", flight_agent_a2a_url=ENDPOINT)
    assert config["A2A_INTERNAL_ENABLED"] is False


def test_api_passes_both_settings_into_the_job():
    """The other half of the plumbing: api.py reads them out of app config."""
    import inspect

    from flaskapp.travel_ai import api

    source = inspect.getsource(api.create_travel_plan)
    assert '"flight_agent_transport": current_app.config.get("FLIGHT_AGENT_TRANSPORT")' in source
    assert '"flight_agent_a2a_url": current_app.config.get("FLIGHT_AGENT_A2A_URL")' in source
