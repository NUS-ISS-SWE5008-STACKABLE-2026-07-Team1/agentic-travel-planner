"""Versioned HTTP API for travel planning and audit retrieval."""

from __future__ import annotations

import json

from flask import Blueprint, current_app, jsonify, request, session
from pydantic import ValidationError

from flaskapp.travel_ai.safeguards import SafetyError, validate_request
from flaskapp.travel_ai.jobs import cancel_job, get_job, submit_plan
from flaskapp.database import get_admin_activity
from flaskapp.admin_auth import is_admin_email

travel_api_bp = Blueprint("travel_api", __name__)


@travel_api_bp.before_request
def require_browser_session():
    """Protect API spend and traces with the current browser session."""
    if not session.get("authenticated"):
        return jsonify(error="Authentication required"), 401
    return None


@travel_api_bp.post("/travel-plans")
def create_travel_plan():
    required = ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")
    if current_app.config.get("LLM_PROVIDER") != "azure" or any(
        not current_app.config.get(name) for name in required
    ):
        return jsonify(error="Azure OpenAI configuration is incomplete"), 503
    try:
        payload = request.get_json(force=False, silent=False)
        if not isinstance(payload, dict):
            return jsonify(error="A JSON object is required"), 400
        travel_request = validate_request(payload, current_app.config["MAX_INPUT_CHARS"])
        settings = {
            "api_key": current_app.config["AZURE_OPENAI_API_KEY"],
            "endpoint": current_app.config["AZURE_OPENAI_ENDPOINT"],
            "deployment": current_app.config["AZURE_OPENAI_DEPLOYMENT"],
            "api_version": current_app.config["AZURE_OPENAI_API_VERSION"],
            "temperature": current_app.config["AZURE_OPENAI_TEMPERATURE"],
            "timeout": current_app.config["AI_REQUEST_TIMEOUT_SECONDS"],
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
    return jsonify(requests=get_admin_activity(current_app.config["DATABASE"]))


@travel_api_bp.get("/traces/<uuid:request_id>")
def get_trace(request_id):
    """Return sanitized decision metadata; protect this endpoint with auth in production."""
    path = current_app.config["TRACE_DIR"] / f"{request_id}.jsonl"
    if not path.is_file():
        return jsonify(error="Trace not found"), 404
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return jsonify(request_id=str(request_id), events=events)
