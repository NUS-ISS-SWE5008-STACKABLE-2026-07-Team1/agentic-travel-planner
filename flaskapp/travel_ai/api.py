"""Versioned HTTP API for travel planning and audit retrieval."""

from __future__ import annotations

import json
import re

from flask import Blueprint, current_app, jsonify, request, session
from pydantic import ValidationError
from werkzeug.security import generate_password_hash

from flaskapp.travel_ai.safeguards import SafetyError, validate_request
from flaskapp.travel_ai.jobs import cancel_job, get_job, submit_plan
from flaskapp.database import (
    get_admin_activity, get_admin_token_summary, get_admins, get_platform_dashboard,
    get_recent_feedback, get_system_logs, owns_request, register_admin, save_plan_feedback,
)
from flaskapp.admin_auth import is_admin_email
from flaskapp.config import get_llm_settings

travel_api_bp = Blueprint("travel_api", __name__)


@travel_api_bp.before_request
def require_browser_session():
    """Protect API spend and traces with the current browser session."""
    if not session.get("authenticated"):
        return jsonify(error="Authentication required"), 401
    return None


@travel_api_bp.post("/travel-plans")
def create_travel_plan():
    llm_settings, configuration_error = get_llm_settings(current_app.config)
    if configuration_error:
        return jsonify(error=configuration_error), 503
    try:
        payload = request.get_json(force=False, silent=False)
        if not isinstance(payload, dict):
            return jsonify(error="A JSON object is required"), 400
        travel_request = validate_request(payload, current_app.config["MAX_INPUT_CHARS"])
        settings = {
            **llm_settings,
            "trace_dir": str(current_app.config["TRACE_DIR"]),
            "database_path": str(current_app.config["DATABASE"]),
        }
        job = submit_plan(travel_request, settings, session.get("user_id"))
        return jsonify(
            request_id=job.request_id,
            status=job.status,
            chat_url=f"/chat/{job.request_id}",
        ), 202
    except (ValidationError, SafetyError) as exc:
        details = exc.errors() if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        return jsonify(error="Invalid travel request", details=details), 422
    except Exception:
        current_app.logger.exception("Travel planning failed")
        return jsonify(error="Travel planning failed", retryable=True), 502


@travel_api_bp.get("/travel-plans/<uuid:request_id>/status")
def get_travel_plan_status(request_id):
    job = get_job(str(request_id), session.get("user_id"))
    if job is None:
        return jsonify(error="Planning job not found"), 404
    body = {"request_id": job.request_id, "status": job.status}
    if job.response is not None:
        body["response"] = job.response.model_dump(mode="json")
    if job.error:
        body["error"] = (
            "Azure took too long to respond. Please retry the plan."
            if job.error == "APITimeoutError"
            else "Travel planning failed"
        )
    return jsonify(body)


@travel_api_bp.post("/travel-plans/<uuid:request_id>/cancel")
def cancel_travel_plan(request_id):
    job = cancel_job(str(request_id), session.get("user_id"))
    if job is None:
        return jsonify(error="Planning job not found"), 404
    return jsonify(request_id=job.request_id, status=job.status)


@travel_api_bp.get("/admin/activity")
def get_admin_monitoring_activity():
    if not is_admin_email(current_app.config, session.get("user_email")):
        return jsonify(error="Administrator access required"), 403
    database = current_app.config["DATABASE"]
    return jsonify(
        requests=get_admin_activity(database),
        consumption=get_admin_token_summary(database),
        platform=get_platform_dashboard(database),
        logs=get_system_logs(database),
        admins=get_admins(database),
        feedback=get_recent_feedback(database),
        prompts=_prompt_catalog(),
    )


def _prompt_catalog():
    from flaskapp.travel_ai.agents.accessibility_agent.prompt import INSTRUCTION as accessibility
    from flaskapp.travel_ai.agents.flight_agent.prompt import INSTRUCTION as flight
    from flaskapp.travel_ai.agents.hotel_transport_agent.prompt import INSTRUCTION as hotel
    from flaskapp.travel_ai.agents.orchestrator_agent.prompt import INSTRUCTION as orchestrator
    from flaskapp.travel_ai.agents.risk_advisory_agent.prompt import INSTRUCTION as risk
    from flaskapp.travel_ai.safeguards import PROMPT_INJECTION, SENSITIVE_KEYS
    return [
        {"agent": "Flight agent", "instruction": flight},
        {"agent": "Hotel & transport agent", "instruction": hotel},
        {"agent": "Accessibility agent", "instruction": accessibility},
        {"agent": "Risk & advisory agent", "instruction": risk},
        {"agent": "Orchestrator agent", "instruction": orchestrator},
        {"agent": "Shared deterministic guardrails", "instruction":
         f"Reject oversized input, sensitive ranking fields ({', '.join(sorted(SENSITIVE_KEYS))}), "
         f"and prompt-injection patterns matching: {PROMPT_INJECTION.pattern}"},
    ]


@travel_api_bp.post("/admin/administrators")
def create_administrator():
    if not is_admin_email(current_app.config, session.get("user_email")):
        return jsonify(error="Administrator access required"), 403
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify(error="A name and valid email address are required"), 422
    if len(password) < 12 or not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password) or not re.search(r"\d", password):
        return jsonify(error="Password must be at least 12 characters with upper-case, lower-case, and numeric characters"), 422
    administrator = register_admin(
        current_app.config["DATABASE"], name, email, generate_password_hash(password)
    )
    return jsonify(administrator=administrator), 201


@travel_api_bp.post("/travel-plans/<uuid:request_id>/feedback")
def submit_plan_feedback(request_id):
    payload = request.get_json(silent=True) or {}
    rating = payload.get("rating")
    comment = str(payload.get("comment", "")).strip()
    if rating not in {"up", "down"}:
        return jsonify(error="Rating must be thumbs up or thumbs down"), 422
    if rating == "down" and len(comment.split()) < 10:
        return jsonify(error="Please provide at least 10 words of feedback"), 422
    feedback = save_plan_feedback(
        current_app.config["DATABASE"], str(request_id), session.get("user_id"),
        rating, comment or None,
    )
    if feedback is None:
        return jsonify(error="Completed planning request not found"), 404
    return jsonify(feedback=feedback), 201


@travel_api_bp.get("/traces/<uuid:request_id>")
def get_trace(request_id):
    """Return sanitized decision metadata to the user who owns the request.

    The ownership check is the substance of this handler. The blueprint's
    before_request establishes only that *somebody* is signed in; it says
    nothing about whose request this is. Without the check below, any signed-in
    user could read any trace whose id they had — from a shared /chat/<uuid>
    link, a referrer header, or the admin activity view.

    404 rather than 403, deliberately, and it matches every sibling endpoint: a
    distinct 403 would make this a probe for whether a given request id exists.
    Not-yours and not-here should be indistinguishable from outside.
    """
    if not owns_request(
        current_app.config["DATABASE"], str(request_id), session.get("user_id")
    ):
        return jsonify(error="Trace not found"), 404
    path = current_app.config["TRACE_DIR"] / f"{request_id}.jsonl"
    if not path.is_file():
        return jsonify(error="Trace not found"), 404
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return jsonify(request_id=str(request_id), events=events)
