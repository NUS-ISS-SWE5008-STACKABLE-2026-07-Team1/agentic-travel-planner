"""Background travel-plan jobs, with their state in the database.

A plan's status, outcome and response live in `planning_jobs`, so any web pod
can answer the browser's status poll, cancel a plan another pod is running, and
report a plan whose pod died. Until 2026-10 they lived in this module's dicts,
which made a second web pod answer 404 for every plan its sibling was running
(ADR-0005) and capped the web role at one pod.

A pod keeps only what cannot be shared: the thread running each of its own
plans and the Event that stops it. Those are never read to answer a request.

Two things cross pods through the database:

- **Cancellation.** The cancel request marks the row cancelled on whichever pod
  receives it. The pod running the plan learns of it from its heartbeat, at
  most `HEARTBEAT_SECONDS` later, and sets the Event the graph checks between
  stages.
- **Liveness.** Each pod refreshes `heartbeat_at` on its active plans every
  `HEARTBEAT_SECONDS`. A plan whose heartbeat is older than `LOST_AFTER_SECONDS`
  belonged to a pod that died or was removed; the next status poll marks it
  failed (`WorkerLost`) instead of leaving it "processing" forever.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from flaskapp.database import (
    ACTIVE_JOB_STATUSES,
    cancel_planning_job,
    create_planning_job,
    fail_lost_planning_job,
    finish_planning_job,
    get_planning_job,
    heartbeat_planning_jobs,
    start_planning_job,
)
from flaskapp.travel_ai.cancellation import PlanningCancelled
from flaskapp.travel_ai.schemas import TravelRequest
from flaskapp.travel_ai.service import TravelPlanningService

# The pod's name on Kubernetes (HOSTNAME is set to it), so `planning_jobs.worker`
# says which pod ran each plan.
WORKER = os.environ.get("HOSTNAME") or socket.gethostname()
HEARTBEAT_SECONDS = 10.0
# Nine missed heartbeats. Long enough that a slow database write never fails a
# live plan; short enough that a traveller whose pod vanished learns within
# about a minute and a half rather than never.
LOST_AFTER_SECONDS = 90.0
# Plans this pod runs at once; more wait in the executor's queue. This, not
# CPU, is what fills a web pod: a plan spends nearly all its time waiting on
# the model.
MAX_CONCURRENT_PLANS = 4


@dataclass
class PlanningJob:
    request_id: str
    user_id: int | None
    status: str = "queued"
    # The PlanResponse as JSON, exactly as the status endpoint returns it.
    response: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class _LocalRun:
    database_path: str
    cancel_event: threading.Event
    future: Future | None = None


# This pod's own threads only. Never consulted for a status answer.
_running: dict[str, _LocalRun] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_PLANS, thread_name_prefix="travel-planner"
)
_heartbeat_thread: threading.Thread | None = None
_logger = logging.getLogger(__name__)


def submit_plan(request: TravelRequest, settings: dict, user_id: int | None,
                request_id: str | None = None) -> PlanningJob:
    request_id = request_id or str(uuid4())
    database_path = settings["database_path"]
    create_planning_job(
        database_path, request_id, user_id, request.model_dump(mode="json"),
        worker=WORKER, heartbeat_at=time.time(),
    )
    with _lock:
        run = _LocalRun(database_path, threading.Event())
        _running[request_id] = run
        run.future = _executor.submit(_run_plan, request_id, request, settings, user_id)
    _ensure_heartbeat()
    return PlanningJob(request_id=request_id, user_id=user_id)


def _run_plan(request_id: str, request: TravelRequest, settings: dict,
              user_id: int | None) -> None:
    with _lock:
        run = _running[request_id]
    database_path = settings["database_path"]
    try:
        # False when it was cancelled while queued, here or on another pod.
        if run.cancel_event.is_set() or not start_planning_job(database_path, request_id):
            return
        service = TravelPlanningService(
            provider=settings["provider"], api_key=settings["api_key"], model=settings["model"],
            endpoint=settings.get("endpoint"), api_version=settings.get("api_version"),
            base_url=settings.get("base_url"),
            temperature=settings["temperature"], timeout=settings["timeout"],
            trace_dir=Path(settings["trace_dir"]),
            # NOT Path(...): this may be a postgresql:// DSN, and Path would
            # normalise the // away and silently turn it back into SQLite.
            database_path=database_path,
            user_id=user_id,
            cancel_event=run.cancel_event,
            guardrail_settings=settings.get("guardrail"),
            input_guardrail=settings.get("input_guardrail"),
            a2a_base_url=settings.get("a2a_base_url"),
            flight_agent_transport=settings.get("flight_agent_transport"),
            flight_agent_a2a_url=settings.get("flight_agent_a2a_url"),
        )
        response = service.create_plan(request, request_id=request_id)
        if run.cancel_event.is_set():
            raise PlanningCancelled("Planning was cancelled")
        # A cancel that landed meanwhile already owns the row; this is a no-op.
        finish_planning_job(
            database_path, request_id, "completed",
            response=response.model_dump(mode="json"),
        )
    except Exception as exc:
        # A cancellation was written by whoever cancelled; nothing to record.
        if not (isinstance(exc, PlanningCancelled) or run.cancel_event.is_set()):
            _logger.exception("Background travel planning failed for request %s", request_id)
            finish_planning_job(
                database_path, request_id, "failed", error_type=type(exc).__name__
            )
    finally:
        with _lock:
            _running.pop(request_id, None)


def get_job(database_path: str, request_id: str, user_id: int | None) -> PlanningJob | None:
    """The job as the database records it, from whichever pod asks."""
    row = get_planning_job(database_path, request_id)
    if row is None or user_id is None or row["user_id"] != user_id:
        return None
    if row["status"] in ACTIVE_JOB_STATUSES and _is_lost(row):
        fail_lost_planning_job(database_path, request_id, time.time() - LOST_AFTER_SECONDS)
        row = get_planning_job(database_path, request_id) or row
    response = None
    if row["status"] == "completed" and row["response_json"]:
        response = json.loads(row["response_json"])
    return PlanningJob(
        request_id=row["request_id"], user_id=row["user_id"], status=row["status"],
        response=response, error=row["error_type"],
    )


def cancel_job(database_path: str, request_id: str, user_id: int | None) -> PlanningJob | None:
    status = cancel_planning_job(database_path, request_id, user_id)
    if status is None:
        return None
    if status == "cancelled":
        # Fast path when the plan runs on this pod; otherwise its own pod picks
        # the cancellation up from the database on its next heartbeat.
        _stop_local(request_id)
    return PlanningJob(request_id=request_id, user_id=user_id, status=status)


def _is_lost(row: dict[str, Any]) -> bool:
    heartbeat = row["heartbeat_at"]
    return heartbeat is None or heartbeat < time.time() - LOST_AFTER_SECONDS


def _stop_local(request_id: str) -> None:
    with _lock:
        run = _running.get(request_id)
    if run is not None:
        run.cancel_event.set()
        if run.future is not None:
            run.future.cancel()  # only has an effect while still queued


def _ensure_heartbeat() -> None:
    # Started lazily, from the first submission, rather than at import: under
    # gunicorn a thread started before the worker forks does not survive it.
    global _heartbeat_thread
    with _lock:
        if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
            return
        _heartbeat_thread = threading.Thread(
            target=_heartbeat_loop, name="planning-heartbeat", daemon=True
        )
        _heartbeat_thread.start()


def _heartbeat_loop() -> None:
    while True:
        time.sleep(HEARTBEAT_SECONDS)
        heartbeat_once()


def heartbeat_once() -> None:
    """Refresh this pod's active plans and stop any cancelled elsewhere."""
    with _lock:
        by_database: dict[str, list[str]] = {}
        for request_id, run in _running.items():
            by_database.setdefault(run.database_path, []).append(request_id)
    for database_path, request_ids in by_database.items():
        try:
            cancelled = heartbeat_planning_jobs(database_path, request_ids, time.time())
        except Exception:
            # One missed beat is harmless; nine in a row is what LOST means.
            _logger.warning("Planning heartbeat failed", exc_info=True)
            continue
        for request_id in cancelled:
            _stop_local(request_id)
