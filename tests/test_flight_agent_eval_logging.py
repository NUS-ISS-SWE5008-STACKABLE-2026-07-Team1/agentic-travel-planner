"""Verifies flight_node actually writes to flight_agent_eval_runs.

Exercises the write path added in agent.py/database.py: `_log_eval_run` ->
`save_flight_eval_run` -> INSERT INTO flight_agent_eval_runs. Not a golden/
pinned test — just confirms the mechanism lands a correctly-shaped row on
both the grounded-success path and the no-inventory (Path 2) fallback, using
a real SQLite database (schema-created via `initialize`), never a live model.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from flaskapp.database import connect, initialize
from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.agents.flight_agent.agent import NAME, create_node
from flaskapp.travel_ai.agents.flight_agent.providers.base import InventoryResult
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightAgentResponse
from flaskapp.travel_ai.schemas import AgentFinding, TravelRequest
from flaskapp.travel_ai.tracing import AuditTracer

# Singapore -> Japan is stocked by the seed dataset (see test_flight_node_providers.py).
SEED_TRIP = dict(
    origin="Singapore", destination="Japan",
    departure_date=date(2026, 8, 28), return_date=date(2026, 9, 2),
    travellers=1, traveller_ages=[34], traveller_genders=["female"],
    traveller_accessibility_needs=[[]],
    budget=4000, currency="SGD",
    preferences=[], accessibility_needs=[],
)


class StubStructured:
    def __init__(self, response):
        self._response = response

    def invoke(self, messages, config=None):
        return self._response


class StubLLM:
    def __init__(self, flight_response, finding):
        self.flight_response = flight_response
        self.finding = finding

    def with_structured_output(self, schema, method=None):
        return StubStructured(
            self.flight_response if schema is FlightAgentResponse else self.finding
        )


def _llm():
    return StubLLM(
        flight_response=FlightAgentResponse(
            rationale="Both legs are direct.", highlighted_flight_ids=[], confidence=0.8
        ),
        finding=AgentFinding(
            agent=NAME, summary="Estimated routings.", options=[], warnings=[], confidence=0.4
        ),
    )


class NoInventoryProvider:
    """Never covers anything — forces the Path 2 (no-inventory) fallback."""

    name = "seed"
    assumption = "test"

    def covers(self, request) -> bool:
        return False

    def fetch(self, request) -> InventoryResult:
        return InventoryResult()


def _state(db_path, payload: dict) -> tuple[str, dict]:
    request = TravelRequest(**payload)
    correlation_id = uuid4()
    request_id = str(correlation_id)
    message = request_message(
        correlation_id=correlation_id, sender="orchestrator_agent", recipient=NAME,
        payload_type="TravelRequest", payload=request,
    )
    # Both FKs must be satisfied before flight_node runs, same as production:
    # flight_agent_eval_runs.request_id -> travel_requests(id), and
    # save_agent_run's own pre-existing write -> agent_runs.request_id ->
    # planning_jobs(request_id) (this node calls save_agent_run before it
    # ever reaches the new eval-logging call).
    with connect(db_path) as db:
        db.execute(
            """INSERT INTO travel_requests
               (id, destination, departure_date, return_date, travellers, currency, risk_tolerance)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (request_id, request.destination, str(request.departure_date),
             str(request.return_date), request.travellers, request.currency, "moderate"),
        )
        db.execute(
            "INSERT INTO planning_jobs (request_id, status, request_json) VALUES (?, 'processing', '{}')",
            (request_id,),
        )
    return request_id, {
        "request_id": request_id,
        "request": request.model_dump(mode="json"),
        "findings": [],
        "messages": [message],
    }


def _eval_rows(db_path, request_id: str) -> list[dict]:
    with connect(db_path) as db:
        return [
            dict(row) for row in db.execute(
                "SELECT * FROM flight_agent_eval_runs WHERE request_id = ?", (request_id,)
            ).fetchall()
        ]


def test_a_successful_grounded_run_writes_one_eval_row(tmp_path):
    db_path = tmp_path / "eval.sqlite3"
    initialize(db_path)
    tracer = AuditTracer(tmp_path, "req-success", database_path=db_path)
    request_id, state = _state(db_path, SEED_TRIP)

    result = create_node(_llm(), tracer)(state)
    assert result["findings"][0].options, "sanity check: the grounded path actually ran"

    rows = _eval_rows(db_path, request_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["run_type"] == "production"
    assert row["outcome"] == "success"
    assert row["flight_agent_mode"] in {"structured", "agentic", "auto"}
    assert row["inventory_source"] == "seed"
    assert row["options_returned"] == len(result["findings"][0].options)
    assert row["latency_ms"] >= 0
    assert row["guardrail_input_result"] == "pass"
    assert row["guardrail_grounding_result"] == "pass"
    assert row["trace_id"] == request_id


def test_the_no_inventory_fallback_writes_a_path2_row(tmp_path):
    db_path = tmp_path / "eval.sqlite3"
    initialize(db_path)
    tracer = AuditTracer(tmp_path, "req-path2", database_path=db_path)
    request_id, state = _state(db_path, SEED_TRIP)

    create_node(_llm(), tracer, provider=NoInventoryProvider())(state)

    rows = _eval_rows(db_path, request_id)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "path2_fallback"
    assert rows[0]["options_returned"] == 0


def test_a_tracer_without_a_database_path_does_not_raise(tmp_path):
    """Mirrors every other DB write in this node (`save_agent_run` etc.):
    when the tracer has no database_path (e.g. most unit tests), logging is a
    no-op, not a crash."""
    tracer = AuditTracer(tmp_path, "req-no-db")
    request = TravelRequest(**SEED_TRIP)
    correlation_id = uuid4()
    message = request_message(
        correlation_id=correlation_id, sender="orchestrator_agent", recipient=NAME,
        payload_type="TravelRequest", payload=request,
    )
    state = {
        "request_id": str(correlation_id),
        "request": request.model_dump(mode="json"),
        "findings": [],
        "messages": [message],
    }
    result = create_node(_llm(), tracer)(state)
    assert result["findings"][0].options
