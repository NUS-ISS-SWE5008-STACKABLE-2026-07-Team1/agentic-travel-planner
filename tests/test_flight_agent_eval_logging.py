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

from flaskapp.database import connect, ensure_planning_job, initialize
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
    # Only what production has when flight_node runs: the planning_jobs row
    # (from jobs.submit_plan, or the A2A entry point's ensure_planning_job).
    # Deliberately NOT a travel_requests row — save_plan writes that after the
    # whole graph finishes, and a fixture that pre-inserted it is what let an
    # FK onto travel_requests pass here while failing every live run.
    ensure_planning_job(db_path, request_id, None, request.model_dump(mode="json"))
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


def test_the_eval_row_lands_before_save_plan_has_written_travel_requests(tmp_path):
    """The live ordering, with SQLite foreign keys enforced.

    flight_node runs mid-plan; `travel_requests` only gains its row in
    `save_plan`, after the whole graph. An FK onto that table failed every
    production write (swallowed into `agent_eval_log_failed`), so this pins
    both halves: the row lands, and nothing was quietly swallowed.
    """
    db_path = tmp_path / "eval.sqlite3"
    initialize(db_path)
    tracer = AuditTracer(tmp_path, "req-before-save-plan", database_path=db_path)
    request_id, state = _state(db_path, SEED_TRIP)

    with connect(db_path) as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM travel_requests WHERE id = ?", (request_id,)
        ).fetchone()[0] == 0, "precondition: save_plan has not run yet"

    create_node(_llm(), tracer)(state)

    assert "agent_eval_log_failed" not in tracer.path.read_text(encoding="utf-8")
    rows = _eval_rows(db_path, request_id)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"
