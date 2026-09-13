"""The A2A executor against a REAL database, running the REAL flight node.

**The defining property of this file is that `AuditTracer` is built with a real
`database_path`.** Do not "simplify" it to `None`.

Every other flight test builds `AuditTracer(tmp_path, "req-1")` — two arguments,
so `database_path` is None, so every `if tracer.database_path:` guard
short-circuits and no test in the suite has ever executed the persistence
writes. That is precisely why this bug shipped:

`agent_runs.request_id` is `REFERENCES planning_jobs(request_id)`
(`database.py`) and `PRAGMA foreign_keys = ON` is set on every SQLite
connection. The A2A path passes the A2A task id as `request_id` and never
creates a `planning_jobs` row, so `save_agent_run` raises IntegrityError, which
`SpecialistAgentExecutor`'s blanket `except Exception` converts into an opaque
failed task. The traveller-facing symptom is "the flight agent just fails over
A2A", with nothing in the error saying why.

The node factory here is the real `create_node`, not a lambda returning a canned
finding. A stub node would exercise the executor's plumbing and none of the
persistence, which is the half that breaks.
"""

from __future__ import annotations

import asyncio

import pytest
from a2a.helpers import new_data_message
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Role, SendMessageRequest, TaskState, TaskStatusUpdateEvent

from flaskapp import database
from flaskapp.config import Config
from flaskapp.travel_ai.a2a_standard import ExecutorContext, SpecialistAgentExecutor
from flaskapp.travel_ai.agents.flight_agent.agent import NAME, create_node
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightAgentResponse
from flaskapp.travel_ai.tracing import AuditTracer

# A route and date range the seed provider actually stocks, so the grounded path
# runs and `save_agent_run` is reached. An uncovered route would fall back to the
# prompt-only path and prove nothing about persistence.
COVERED_PAYLOAD = {
    "origin": "Singapore",
    "destination": "Japan",
    "departure_date": "2026-10-10",
    "return_date": "2026-10-16",
    "travellers": 1,
    "traveller_ages": [30],
    "traveller_genders": ["prefer_not_to_say"],
    "traveller_accessibility_needs": [[]],
    "budget": 3000,
}


class RecordingQueue:
    """Captures the events an executor enqueues, in order."""

    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


class _StubStructured:
    def __init__(self, response):
        self._response = response

    def invoke(self, messages, config=None):
        return self._response


class _StubLLM:
    """Serves the grounded path; the fallback path is not exercised here."""

    def with_structured_output(self, schema, method=None):
        if schema is FlightAgentResponse:
            return _StubStructured(FlightAgentResponse(
                rationale="Chose the earliest arriving pair.",
                highlighted_flight_ids=[],
                confidence=0.8,
            ))
        return _StubStructured(None)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "planner.sqlite3"
    database.initialize(path)
    return path


def _real_node_factory(tmp_path, database_path):
    """The real flight node, on seed inventory, with persistence switched on."""
    config = {
        **vars(Config),
        "FLIGHT_AGENT_MODE": "structured",
        "FLIGHT_INVENTORY_SOURCE": "seed",
    }

    def node_factory(request_id):
        tracer = AuditTracer(tmp_path, request_id, database_path)
        return create_node(_StubLLM(), tracer, config=config)

    return node_factory


def _run(executor, payload=None):
    message = new_data_message(payload or COVERED_PAYLOAD, role=Role.ROLE_USER)
    context = RequestContext(
        ServerCallContext(), request=SendMessageRequest(message=message)
    )
    queue = RecordingQueue()
    asyncio.run(executor.execute(context, queue))
    return queue


def _terminal_state(queue):
    status_events = [e for e in queue.events if isinstance(e, TaskStatusUpdateEvent)]
    assert status_events, "executor emitted no terminal status event"
    return status_events[-1].status.state


def test_real_node_completes_when_persistence_is_enabled(tmp_path, database_path):
    """The regression. Fails with FOREIGN KEY constraint failed before the fix."""
    executor = SpecialistAgentExecutor(
        NAME, _real_node_factory(tmp_path, database_path),
        ExecutorContext(database_path=database_path)
    )

    queue = _run(executor)

    assert _terminal_state(queue) == TaskState.TASK_STATE_COMPLETED


def test_a2a_request_records_a_planning_job(tmp_path, database_path):
    """A2A work is visible in the tables /admin reads, not silently absent."""
    executor = SpecialistAgentExecutor(
        NAME, _real_node_factory(tmp_path, database_path),
        ExecutorContext(database_path=database_path)
    )

    _run(executor)

    with database.connect(database_path) as db:
        jobs = db.execute("SELECT request_id, status FROM planning_jobs").fetchall()
        runs = db.execute(
            "SELECT request_id, agent, status FROM agent_runs"
        ).fetchall()

    assert len(jobs) == 1, "expected exactly one planning_jobs row for the A2A request"
    assert len(runs) == 1, "expected the flight agent's run to be recorded"
    assert runs[0]["agent"] == NAME
    assert runs[0]["status"] == "completed"
    # The run must hang off the job that was created for it, or the FK that
    # caused this bug is simply being bypassed rather than satisfied.
    assert runs[0]["request_id"] == jobs[0]["request_id"]
