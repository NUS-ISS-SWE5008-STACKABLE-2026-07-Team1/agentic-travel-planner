"""The LangGraph node: does the graph actually get grounded flight options?

Every test here uses a stub model. The point is not what the model says — it is
that the candidate list comes from inventory rather than from the model, that a
model failure cannot fabricate a flight, and that an uncoverable route degrades
to a labelled estimate instead of an empty answer.
"""

from datetime import date
from uuid import uuid4

import pytest

from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.agents.flight_agent.agent import ESTIMATE_WARNING, NAME, create_node
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightAgentResponse
from flaskapp.travel_ai.schemas import AgentFinding, TravelRequest
from flaskapp.travel_ai.tracing import AuditTracer

COVERED = dict(
    origin="Singapore", destination="Japan",
    departure_date=date(2026, 9, 1), return_date=date(2026, 9, 5),
    travellers=1, traveller_ages=[34], traveller_genders=["female"],
    traveller_accessibility_needs=[["wheelchair assistance"]],
    budget=4000, currency="SGD",
    preferences=["direct flights"],
    accessibility_needs=["Traveler 1: wheelchair assistance"],
)
UNCOVERED = {**COVERED, "destination": "Brazil"}


class StubStructured:
    """Stands in for `llm.with_structured_output(...)`."""

    def __init__(self, response, record):
        self._response = response
        self._record = record

    def invoke(self, messages, config=None):
        self._record.append(messages)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class StubLLM:
    """Returns a flight response or an AgentFinding depending on the schema
    asked for, so one stub serves both the grounded and fallback paths."""

    def __init__(self, flight_response=None, finding=None):
        self.flight_response = flight_response
        self.finding = finding
        self.calls: list = []

    def with_structured_output(self, schema, method=None):
        payload = self.flight_response if schema is FlightAgentResponse else self.finding
        return StubStructured(payload, self.calls)


def _state(payload: dict) -> dict:
    request = TravelRequest(**payload)
    correlation_id = uuid4()
    message = request_message(
        correlation_id=correlation_id, sender="orchestrator_agent", recipient=NAME,
        payload_type="TravelRequest", payload=request,
    )
    return {
        "request_id": str(correlation_id),
        "request": request.model_dump(mode="json"),
        "findings": [],
        "messages": [message],
    }


@pytest.fixture
def tracer(tmp_path):
    return AuditTracer(tmp_path, "req-1")


def _grounded_llm(**overrides):
    response = FlightAgentResponse(
        **{"rationale": "SQ632 arrives earliest.", "highlighted_flight_ids": [], "confidence": 0.8,
           **overrides}
    )
    return StubLLM(flight_response=response)


def test_options_come_from_inventory_not_the_model(tracer):
    node = create_node(_grounded_llm(), tracer)
    finding = node(_state(COVERED))["findings"][0]

    assert isinstance(finding, AgentFinding) and finding.agent == NAME
    assert finding.options, "grounded path must return candidates"
    # Real seed rows, not anything the stub model produced.
    assert {o.name.split(" ")[0] for o in finding.options} == {
        "SQ636-20260901", "SQ632-20260901", "SQ637-20260905", "SQ633-20260905"
    }
    assert all(o.currency == "SGD" for o in finding.options)
    assert all(o.assumptions for o in finding.options)


def test_wheelchair_need_filters_candidates(tracer):
    """The whole point of the free-text fix: a need typed into the form must
    still exclude flights without assistance once it reaches the node."""
    node = create_node(_grounded_llm(), tracer)
    finding = node(_state(COVERED))["findings"][0]
    assert all("not offered" not in limit for o in finding.options for limit in o.limitations)


def test_model_failure_still_yields_grounded_options(tracer):
    """A dead model degrades the prose, not the answer."""
    llm = StubLLM(flight_response=RuntimeError("model exploded"))
    finding = create_node(llm, tracer)(_state(COVERED))["findings"][0]

    assert finding.options, "candidates survive an LLM failure"
    assert finding.confidence == 0.0
    assert "unavailable" in finding.summary.lower()


def test_hallucinated_flight_id_never_reaches_the_finding(tracer):
    """An invented id triggers retry-then-fallback, so the rationale is
    replaced rather than published."""
    llm = _grounded_llm(highlighted_flight_ids=["TOTALLY-MADE-UP-123"])
    finding = create_node(llm, tracer)(_state(COVERED))["findings"][0]

    assert "TOTALLY-MADE-UP-123" not in finding.summary
    assert finding.confidence == 0.0
    assert finding.options


def test_uncovered_route_falls_back_and_says_so(tracer):
    """Brazil has no inventory. The traveller still gets options, clearly
    labelled as estimates rather than silently passed off as verified."""
    llm = StubLLM(finding=AgentFinding(
        agent=NAME, summary="Estimated routings.", options=[], warnings=[], confidence=0.4,
    ))
    finding = create_node(llm, tracer)(_state(UNCOVERED))["findings"][0]

    assert ESTIMATE_WARNING in finding.warnings
    assert any("Brazil" in w for w in finding.warnings)


def test_a2a_response_is_emitted_for_the_orchestrator(tracer):
    result = create_node(_grounded_llm(), tracer)(_state(COVERED))
    message = result["messages"][0]

    assert message.sender == NAME and message.recipient == "orchestrator_agent"
    assert message.message_type == "response" and message.status == "completed"
    assert message.payload_type == "AgentFinding"


def test_missing_a2a_request_is_rejected(tracer):
    state = _state(COVERED)
    state["messages"] = []
    with pytest.raises(ValueError, match="Missing A2A request"):
        create_node(_grounded_llm(), tracer)(state)


def test_lifecycle_events_are_not_double_recorded(tracer, tmp_path):
    import json

    create_node(_grounded_llm(), tracer)(_state(COVERED))
    events = [json.loads(line)["event"] for line in tracer.path.read_text().splitlines() if line.strip()]

    assert events.count("agent_started") == 1
    assert events.count("agent_completed") == 1


def test_trace_chain_verifies_after_a_run(tracer):
    from flaskapp.travel_ai.tracing import verify_hash_chain

    create_node(_grounded_llm(), tracer)(_state(COVERED))
    assert verify_hash_chain(tracer.path)
