"""In-process background jobs for interactive travel-plan progress."""

from __future__ import annotations

import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from flaskapp.travel_ai.schemas import PlanResponse, TravelRequest
from flaskapp.travel_ai.service import TravelPlanningService
from flaskapp.database import create_planning_job, update_planning_job
from flaskapp.travel_ai.cancellation import PlanningCancelled


@dataclass
class PlanningJob:
    request_id: str
    user_id: int | None
    status: str = "queued"
    response: PlanResponse | None = None
    error: str | None = None


_jobs: dict[str, PlanningJob] = {}
_cancel_events: dict[str, threading.Event] = {}
_futures = {}
_database_paths: dict[str, str] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="travel-planner")
_logger = logging.getLogger(__name__)


def submit_plan(request: TravelRequest, settings: dict, user_id: int | None,
                request_id: str | None = None) -> PlanningJob:
    job = PlanningJob(request_id=request_id or str(uuid4()), user_id=user_id)
    create_planning_job(
        settings["database_path"], job.request_id, user_id, request.model_dump(mode="json")
    )
    with _lock:
        _jobs[job.request_id] = job
        _cancel_events[job.request_id] = threading.Event()
        _database_paths[job.request_id] = settings["database_path"]
        _futures[job.request_id] = _executor.submit(
            _run_plan, job.request_id, request, settings, user_id
        )
    return job


def _run_plan(request_id: str, request: TravelRequest, settings: dict,
              user_id: int | None) -> None:
    with _lock:
        _jobs[request_id].status = "processing"
    update_planning_job(settings["database_path"], request_id, "processing")
    try:
        service = TravelPlanningService(
            provider=settings["provider"], api_key=settings["api_key"], model=settings["model"],
            endpoint=settings.get("endpoint"), api_version=settings.get("api_version"),
            base_url=settings.get("base_url"),
            temperature=settings["temperature"], timeout=settings["timeout"],
            trace_dir=Path(settings["trace_dir"]),
            # NOT Path(...): this may be a postgresql:// DSN, and Path would
            # normalise the // away and silently turn it back into SQLite.
            database_path=settings["database_path"],
            user_id=user_id,
            cancel_event=_cancel_events[request_id],
            guardrail_settings=settings.get("guardrail"),
            input_guardrail=settings.get("input_guardrail"),
        )
        response = service.create_plan(request, request_id=request_id)
        with _lock:
            if _cancel_events[request_id].is_set():
                raise PlanningCancelled("Planning was cancelled")
            _jobs[request_id].response = response
            _jobs[request_id].status = "completed"
        update_planning_job(settings["database_path"], request_id, "completed")
    except Exception as exc:
        cancelled = isinstance(exc, PlanningCancelled) or _cancel_events[request_id].is_set()
        if not cancelled:
            _logger.exception("Background travel planning failed for request %s", request_id)
        with _lock:
            _jobs[request_id].status = "cancelled" if cancelled else "failed"
            _jobs[request_id].error = None if cancelled else type(exc).__name__
        update_planning_job(
            settings["database_path"], request_id, "cancelled" if cancelled else "failed",
            None if cancelled else type(exc).__name__,
        )


def get_job(request_id: str, user_id: int | None) -> PlanningJob | None:
    with _lock:
        job = _jobs.get(request_id)
        return job if job and job.user_id == user_id else None


def cancel_job(request_id: str, user_id: int | None) -> PlanningJob | None:
    with _lock:
        job = _jobs.get(request_id)
        if not job or job.user_id != user_id:
            return None
        if job.status in {"completed", "failed", "cancelled"}:
            return job
        _cancel_events[request_id].set()
        _futures[request_id].cancel()
        job.status = "cancelled"
        database_path = _database_paths[request_id]
    update_planning_job(database_path, request_id, "cancelled")
    return job
