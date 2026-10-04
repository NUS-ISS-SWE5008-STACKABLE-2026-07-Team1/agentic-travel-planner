"""Token usage reaches `agent_runs` for the agents that make their own model calls.

Flight, Hotel & Transport and Risk & Advisory each created a `TokenUsageCallback`
and saved its totals, but never passed it to the model, so every run on their
grounded paths recorded zero tokens and the admin page showed none. These tests
read the `agent_runs` row the admin page sums, in a real SQLite database.

The stub model reports usage the way a real chat model does: to the callbacks it
was invoked with, and to nothing else. A node that forgets to pass its counter
therefore records zero here exactly as it did in production.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import AIMessage

from flaskapp.database import connect, ensure_planning_job, initialize
from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.agents.flight_agent import agent as flight
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightAgentResponse
from flaskapp.travel_ai.agents.hotel_transport_agent import agent as hotel
from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import HotelTransportResponse
from flaskapp.travel_ai.agents.risk_advisory_agent import agent as risk
from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskAgentResponse
from flaskapp.travel_ai.schemas import AgentFinding, TravelRequest
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.usage import TokenUsageCallback
from tests.test_risk_advisory_agent import FakeProvider

PER_CALL = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}

# Covered by the flight, hotel and risk seed data alike.
TRIP = dict(
    origin="Singapore", destination="Japan", origin_city="Singapore", destination_city="Tokyo",
    departure_date=date(2026, 8, 28), return_date=date(2026, 9, 2),
    travellers=1, traveller_ages=[34], traveller_genders=["female"],
    traveller_accessibility_needs=[[]], budget=4000, currency="SGD",
)


def _report_usage(config) -> None:
    """What a real chat model does after each call."""
    response = SimpleNamespace(generations=[[SimpleNamespace(
        message=SimpleNamespace(usage_metadata=dict(PER_CALL))
    )]])
    for callback in (config or {}).get("callbacks") or []:
        callback.on_llm_end(response)


class PaidStructured:
    def __init__(self, response):
        self._response = response

    def invoke(self, messages, config=None):
        _report_usage(config)
        return self._response


class PaidBound:
    """A tool-bound model that searches once, then stops."""

    def __init__(self):
        self.calls = 0

    def invoke(self, messages, config=None):
        _report_usage(config)
        self.calls += 1
        if self.calls == 1:
            return AIMessage(content="", tool_calls=[{
                "name": "search_flights", "args": {"direction": "OUTBOUND"},
                "id": "call-1", "type": "tool_call",
            }])
        return AIMessage(content="done")


class PaidLLM:
    def __init__(self, responses: dict, *, tools: bool = False):
        self._responses = responses
        if tools:
            self.bound = PaidBound()
            self.bind_tools = lambda specs: self.bound

    def with_structured_output(self, schema, method="json_schema"):
        return PaidStructured(self._responses[schema])


def _run(tmp_path, module, llm, **node_kwargs) -> dict:
    """Run one agent's node against a real database; return its agent_runs row."""
    db_path = tmp_path / "usage.sqlite3"
    initialize(db_path)
    request = TravelRequest(**TRIP)
    correlation_id = uuid4()
    request_id = str(correlation_id)
    ensure_planning_job(db_path, request_id, None, request.model_dump(mode="json"))
    tracer = AuditTracer(tmp_path, request_id, database_path=db_path)
    state = {
        "request_id": request_id,
        "request": request.model_dump(mode="json"),
        "findings": [],
        "messages": [request_message(
            correlation_id=correlation_id, sender="orchestrator_agent", recipient=module.NAME,
            payload_type="TravelRequest", payload=request,
        )],
    }
    module.create_node(llm, tracer, **node_kwargs)(state)
    with connect(db_path) as db:
        return dict(db.execute(
            "SELECT status, input_tokens, output_tokens, total_tokens FROM agent_runs "
            "WHERE request_id = ? AND agent = ?", (request_id, module.NAME),
        ).fetchone())


def _fallback_finding(agent: str) -> AgentFinding:
    return AgentFinding(agent=agent, summary="s", options=[], warnings=[], confidence=0.3)


def test_flight_single_shot_run_records_its_tokens(tmp_path):
    llm = PaidLLM({
        FlightAgentResponse: FlightAgentResponse(
            rationale="Both legs are direct.", highlighted_flight_ids=[], confidence=0.8
        ),
        AgentFinding: _fallback_finding(flight.NAME),
    })
    row = _run(tmp_path, flight, llm, config={"FLIGHT_AGENT_MODE": "structured"})
    assert row == {"status": "completed", **PER_CALL}


def test_flight_tool_loop_records_every_model_turn(tmp_path):
    """Two loop turns plus the final structured answer: three paid calls."""
    llm = PaidLLM({
        FlightAgentResponse: FlightAgentResponse(
            rationale="Searched, then chose.", highlighted_flight_ids=[], confidence=0.7
        ),
        AgentFinding: _fallback_finding(flight.NAME),
    }, tools=True)
    row = _run(tmp_path, flight, llm, config={"FLIGHT_AGENT_MODE": "agentic"})
    assert llm.bound.calls == 2, "precondition: the loop really ran two turns"
    assert row["total_tokens"] == 3 * PER_CALL["total_tokens"]
    assert row["input_tokens"] == 3 * PER_CALL["input_tokens"]


def test_hotel_transport_run_records_its_tokens(tmp_path):
    llm = PaidLLM({
        HotelTransportResponse: HotelTransportResponse(
            rationale="Close to the station.", highlighted_hotel_ids=[], confidence=0.8
        ),
        AgentFinding: _fallback_finding(hotel.NAME),
    })
    row = _run(tmp_path, hotel, llm)
    assert row == {"status": "completed", **PER_CALL}


def test_risk_advisory_run_records_its_tokens(tmp_path):
    llm = PaidLLM({
        RiskAgentResponse: RiskAgentResponse(
            rationale="Low overall risk.",
            highlighted_risk_ids=["fact-jp-tokyo-crime-safety"],
            confidence=0.7,
        ),
        AgentFinding: _fallback_finding(risk.NAME),
    })
    row = _run(tmp_path, risk, llm, provider=FakeProvider())
    assert row == {"status": "completed", **PER_CALL}


def test_a_missing_total_adds_only_that_calls_tokens():
    """The fallback used the running totals, so a second call without a total
    re-added the first call's tokens."""
    callback = TokenUsageCallback()
    response = SimpleNamespace(generations=[[SimpleNamespace(message=SimpleNamespace(
        usage_metadata={"input_tokens": 100, "output_tokens": 20}
    ))]])
    callback.on_llm_end(response)
    callback.on_llm_end(response)
    assert callback.as_dict() == {"input_tokens": 200, "output_tokens": 40, "total_tokens": 240}
