"""Plan state lives in the database, so any web pod can answer for any plan.

Until 2026-10 a plan's status lived in `jobs.py`'s module dicts: a second web
pod answered 404 for every plan its sibling was running, which capped the web
role at one pod. "Another pod" here is simply code that never saw the plan in
its own memory: a row written straight to the database, or `_running` emptied.
"""

from __future__ import annotations

import threading
import time

import pytest

from flaskapp.database import (
    cancel_planning_job, connect, create_planning_job, finish_planning_job,
    get_planning_job, heartbeat_planning_jobs, initialize, seed_login_user,
    start_planning_job,
)
from flaskapp.travel_ai import jobs
from flaskapp.travel_ai.cancellation import PlanningCancelled
from flaskapp.travel_ai.schemas import PlanResponse, TravelPlan

REQUEST = {"origin": "Singapore", "destination": "Japan"}


@pytest.fixture
def database(tmp_path):
    path = str(tmp_path / "jobs.sqlite3")
    initialize(path)
    seed_login_user(path, "owner@example.com", "hash")
    seed_login_user(path, "someone@example.com", "hash")
    return path


def _user(database, email):
    with connect(database) as db:
        return db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]


@pytest.fixture
def owner(database):
    return _user(database, "owner@example.com")


def _elsewhere(database, request_id, user_id, *, status="processing", heartbeat=None):
    """A plan another pod is running: in the database, not in this pod's memory."""
    create_planning_job(database, request_id, user_id, REQUEST,
                        worker="web-other-pod", heartbeat_at=heartbeat or time.time())
    if status == "processing":
        assert start_planning_job(database, request_id)
    assert request_id not in jobs._running


def _response(request_id):
    return PlanResponse(
        request_id=request_id,
        plan=TravelPlan(title="Kyoto", summary="Five days.", itinerary=["Day 1"],
                        rationale=["Because"]),
        agent_findings=[], trace_url=f"/api/v1/traces/{request_id}",
    )


class _FakeService:
    """Stands in for TravelPlanningService; `behaviour` decides what a plan does."""
    behaviour = staticmethod(lambda self, request_id: _response(request_id))

    def __init__(self, **kwargs):
        self.cancel_event = kwargs["cancel_event"]

    def create_plan(self, request, request_id=None):
        return type(self).behaviour(self, request_id)


def _settings(database):
    return {"database_path": database, "provider": "openai", "api_key": "k", "model": "m",
            "temperature": None, "timeout": 5, "trace_dir": "instance/traces"}


def _wait_until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class _Request:
    def model_dump(self, mode=None):
        return REQUEST


# --- status --------------------------------------------------------------------

def test_any_pod_answers_for_a_plan_running_on_another(database, owner):
    _elsewhere(database, "plan-1", owner)

    job = jobs.get_job(database, "plan-1", owner)

    assert job is not None and job.status == "processing"


def test_a_finished_plan_and_its_response_come_back_from_the_database(database, owner, monkeypatch):
    monkeypatch.setattr(jobs, "TravelPlanningService", _FakeService)

    submitted = jobs.submit_plan(_Request(), _settings(database), owner)
    assert _wait_until(lambda: jobs.get_job(database, submitted.request_id, owner).status == "completed")
    assert submitted.request_id not in jobs._running, "the pod forgets a finished plan"

    job = jobs.get_job(database, submitted.request_id, owner)
    assert job.response["plan"]["title"] == "Kyoto"
    assert get_planning_job(database, submitted.request_id)["worker"] == jobs.WORKER


def test_a_failed_plan_records_its_error_type(database, owner, monkeypatch):
    def explode(self, request_id):
        raise TimeoutError("model too slow")

    monkeypatch.setattr(jobs, "TravelPlanningService",
                        type("Failing", (_FakeService,), {"behaviour": explode}))

    submitted = jobs.submit_plan(_Request(), _settings(database), owner)
    assert _wait_until(lambda: jobs.get_job(database, submitted.request_id, owner).status == "failed")
    assert jobs.get_job(database, submitted.request_id, owner).error == "TimeoutError"


def test_only_the_owner_can_see_a_plan(database, owner):
    _elsewhere(database, "plan-1", owner)

    assert jobs.get_job(database, "plan-1", _user(database, "someone@example.com")) is None
    assert jobs.get_job(database, "plan-1", None) is None, "no session owns nothing"
    assert jobs.get_job(database, "no-such-plan", owner) is None


# --- a pod that disappears -----------------------------------------------------

def test_a_plan_whose_pod_stopped_heartbeating_is_reported_failed(database, owner):
    stale = time.time() - jobs.LOST_AFTER_SECONDS - 5
    _elsewhere(database, "plan-1", owner, heartbeat=stale)

    job = jobs.get_job(database, "plan-1", owner)

    assert (job.status, job.error) == ("failed", "WorkerLost")


def test_a_plan_with_a_recent_heartbeat_is_left_alone(database, owner):
    _elsewhere(database, "plan-1", owner, heartbeat=time.time() - jobs.HEARTBEAT_SECONDS)

    assert jobs.get_job(database, "plan-1", owner).status == "processing"


def test_heartbeats_keep_a_long_plan_alive(database, owner):
    stale = time.time() - jobs.LOST_AFTER_SECONDS - 5
    _elsewhere(database, "plan-1", owner, heartbeat=stale)

    heartbeat_planning_jobs(database, ["plan-1"], time.time())

    assert jobs.get_job(database, "plan-1", owner).status == "processing"


# --- cancellation across pods --------------------------------------------------

def test_a_cancel_on_one_pod_reaches_the_pod_running_the_plan(database, owner):
    _elsewhere(database, "plan-1", owner)

    assert jobs.cancel_job(database, "plan-1", owner).status == "cancelled"
    # What the running pod's next heartbeat learns.
    assert heartbeat_planning_jobs(database, ["plan-1"], time.time()) == {"plan-1"}


def test_finishing_cannot_overwrite_a_cancellation(database, owner):
    _elsewhere(database, "plan-1", owner)
    cancel_planning_job(database, "plan-1", owner)

    assert not finish_planning_job(database, "plan-1", "completed", response={"x": 1})
    assert jobs.get_job(database, "plan-1", owner).status == "cancelled"


def test_cancelling_a_finished_plan_leaves_it_finished(database, owner):
    _elsewhere(database, "plan-1", owner)
    finish_planning_job(database, "plan-1", "completed", response={"x": 1})

    assert jobs.cancel_job(database, "plan-1", owner).status == "completed"


def test_only_the_owner_can_cancel(database, owner):
    _elsewhere(database, "plan-1", owner)

    assert jobs.cancel_job(database, "plan-1", _user(database, "someone@example.com")) is None
    assert jobs.cancel_job(database, "plan-1", None) is None
    assert jobs.get_job(database, "plan-1", owner).status == "processing"


def test_the_heartbeat_stops_a_running_plan_cancelled_elsewhere(database, owner, monkeypatch):
    started = threading.Event()

    def wait_for_cancel(self, request_id):
        started.set()
        if self.cancel_event.wait(5):
            raise PlanningCancelled("stopped")
        return _response(request_id)

    monkeypatch.setattr(jobs, "TravelPlanningService",
                        type("Slow", (_FakeService,), {"behaviour": wait_for_cancel}))

    submitted = jobs.submit_plan(_Request(), _settings(database), owner)
    assert started.wait(5)
    # The cancel arrives on another pod: database only, no local fast path.
    assert cancel_planning_job(database, submitted.request_id, owner) == "cancelled"
    jobs.heartbeat_once()

    assert _wait_until(lambda: submitted.request_id not in jobs._running)
    assert jobs.get_job(database, submitted.request_id, owner).status == "cancelled"


def test_a_plan_cancelled_while_queued_never_starts(database, owner, monkeypatch):
    constructed = []

    class Recording(_FakeService):
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(jobs, "TravelPlanningService", Recording)
    create_planning_job(database, "plan-1", owner, REQUEST, heartbeat_at=time.time())
    cancel_planning_job(database, "plan-1", owner)
    jobs._running["plan-1"] = jobs._LocalRun(database, threading.Event())

    jobs._run_plan("plan-1", _Request(), _settings(database), owner)

    assert constructed == []
    assert "plan-1" not in jobs._running


# --- the API -------------------------------------------------------------------

def test_the_status_endpoint_reads_the_database(tmp_path, monkeypatch):
    from flaskapp import create_app
    from tests.test_api import TestConfig

    class Config(TestConfig):
        DATABASE = tmp_path / "api.sqlite3"

    app = create_app(Config)
    database = str(Config.DATABASE)
    seed_login_user(database, "owner@example.com", "hash")
    owner = _user(database, "owner@example.com")
    request_id = "11111111-1111-4111-8111-111111111111"
    _elsewhere(database, request_id, owner)
    finish_planning_job(database, request_id, "completed",
                        response=_response(request_id).model_dump(mode="json"))

    client = app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_id"] = owner
    body = client.get(f"/api/v1/travel-plans/{request_id}/status").get_json()

    assert body["status"] == "completed"
    assert body["response"]["plan"]["title"] == "Kyoto"


def test_a_lost_plan_tells_the_traveller_to_retry(tmp_path):
    from flaskapp import create_app
    from tests.test_api import TestConfig

    class Config(TestConfig):
        DATABASE = tmp_path / "api.sqlite3"

    app = create_app(Config)
    database = str(Config.DATABASE)
    seed_login_user(database, "owner@example.com", "hash")
    owner = _user(database, "owner@example.com")
    request_id = "11111111-1111-4111-8111-111111111111"
    _elsewhere(database, request_id, owner, heartbeat=time.time() - jobs.LOST_AFTER_SECONDS - 5)

    client = app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_id"] = owner
    body = client.get(f"/api/v1/travel-plans/{request_id}/status").get_json()

    assert body["status"] == "failed"
    assert "restarted" in body["error"]


# --- existing databases --------------------------------------------------------

def test_an_old_database_gains_the_new_columns(tmp_path):
    from flaskapp.database import SCHEMA_SQLITE

    path = str(tmp_path / "old.sqlite3")
    old = SCHEMA_SQLITE.replace(
        "    session_ended_at TEXT,\n    worker TEXT,\n    heartbeat_at REAL,\n    response_json TEXT\n",
        "    session_ended_at TEXT\n",
    )
    assert old != SCHEMA_SQLITE
    with connect(path) as db:
        db.executescript(old)

    initialize(path)

    with connect(path) as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(planning_jobs)")}
    assert {"worker", "heartbeat_at", "response_json"} <= columns
