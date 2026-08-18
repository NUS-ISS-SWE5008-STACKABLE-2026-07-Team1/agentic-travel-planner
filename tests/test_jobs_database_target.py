"""A DSN must reach save_plan intact.

Path("postgresql://host/db") normalises the double slash away, and the result
is no longer recognised as a DSN — so planning silently falls back to SQLite
while the web request path talks to Postgres. Nothing raises.
"""

import threading
from pathlib import Path
from unittest.mock import MagicMock

from flaskapp.database import is_postgres
from flaskapp.travel_ai.jobs import _run_plan, PlanningJob
from flaskapp.travel_ai.schemas import PlanResponse, TravelPlan
import flaskapp.travel_ai.jobs as jobs_module

DSN = "postgresql://user:pass@aws-0-us-west-2.pooler.supabase.com:5432/postgres"


def test_path_round_trip_destroys_a_dsn():
    """Characterises the bug this task fixes."""
    assert not is_postgres(str(Path(DSN)))


def test_service_keeps_a_dsn_usable():
    from flaskapp.travel_ai.service import TravelPlanningService

    service = TravelPlanningService(
        provider="openai", api_key="k", model="m", temperature=None,
        timeout=1, trace_dir=Path("instance/traces"), database_path=DSN,
    )
    assert is_postgres(service.database_path)


def test_jobs_run_plan_preserves_dsn_through_service_instantiation(monkeypatch):
    """Verify that _run_plan passes the DSN to TravelPlanningService without Path() wrapping.

    This test exercises the actual jobs.py code path and would fail if someone
    re-introduced Path(settings["database_path"]) at jobs.py:63.
    """
    # Record what kwargs are passed to TravelPlanningService.__init__
    captured_kwargs = {}

    class MockTravelPlanningService:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)
            # Ensure we have all the fields that would be set
            self.database_path = kwargs.get("database_path")

        def create_plan(self, request, request_id=None):
            # Return a valid PlanResponse with a real minimal TravelPlan
            return PlanResponse(
                request_id=request_id or "test",
                plan=TravelPlan(
                    title="Test Plan",
                    summary="A test travel plan.",
                    itinerary=["Day 1: Arrive", "Day 2: Explore"],
                    rationale=["For testing"],
                ),
                agent_findings=[],
                trace_url="/traces/test",
            )

    # Monkeypatch the service class
    monkeypatch.setattr(
        "flaskapp.travel_ai.jobs.TravelPlanningService",
        MockTravelPlanningService
    )

    # Monkeypatch database functions to no-ops
    monkeypatch.setattr("flaskapp.travel_ai.jobs.update_planning_job", MagicMock())

    # Create minimal settings dict with DSN
    settings = {
        "provider": "openai",
        "api_key": "test-key",
        "model": "gpt-4",
        "temperature": 0.7,
        "timeout": 30.0,
        "trace_dir": "instance/traces",
        "database_path": DSN,  # The DSN string as passed by api.py
    }

    # Create minimal mock request (we don't care about its fields for this test)
    request = MagicMock()

    # Set up the required state and clean up afterwards
    cancel_event = threading.Event()
    test_job = PlanningJob(request_id="test-request-id", user_id=None)

    try:
        jobs_module._jobs["test-request-id"] = test_job
        jobs_module._cancel_events["test-request-id"] = cancel_event

        # Call _run_plan directly
        _run_plan("test-request-id", request, settings, user_id=None)

        # Verify the database_path was passed unchanged (not wrapped in Path)
        assert captured_kwargs["database_path"] == DSN, \
            f"database_path was modified: {captured_kwargs['database_path']}"
        assert is_postgres(captured_kwargs["database_path"]), \
            f"database_path no longer recognized as PostgreSQL DSN"

        # Verify the job reached completion, proving the path executed end-to-end
        assert jobs_module._jobs["test-request-id"].status == "completed", \
            f"job status is {jobs_module._jobs['test-request-id'].status}, expected 'completed'"
    finally:
        # Clean up module globals to prevent test pollution
        jobs_module._jobs.pop("test-request-id", None)
        jobs_module._cancel_events.pop("test-request-id", None)
