"""Versioned HTTP API for travel planning and audit retrieval."""

from __future__ import annotations

import json

from flask import Blueprint, current_app, jsonify, request, session
from pydantic import ValidationError

from flaskapp.travel_ai.safeguards import SafetyError, validate_request
from flaskapp.travel_ai.service import TravelPlanningService

travel_api_bp = Blueprint("travel_api", __name__)


@travel_api_bp.before_request
def require_browser_session():
    """Protect API spend and traces with the current browser session."""
    if not session.get("authenticated"):
        return jsonify(error="Authentication required"), 401
    return None


@travel_api_bp.post("/travel-plans")
def create_travel_plan():
    if not current_app.config.get("OPENAI_API_KEY"):
        return jsonify(error="OPENAI_API_KEY is not configured"), 503
    try:
        payload = request.get_json(force=False, silent=False)
        if not isinstance(payload, dict):
            return jsonify(error="A JSON object is required"), 400
        travel_request = validate_request(payload, current_app.config["MAX_INPUT_CHARS"])
        service = TravelPlanningService(
            api_key=current_app.config["OPENAI_API_KEY"],
            model=current_app.config["OPENAI_MODEL"],
            temperature=current_app.config["OPENAI_TEMPERATURE"],
            timeout=current_app.config["AI_REQUEST_TIMEOUT_SECONDS"],
            trace_dir=current_app.config["TRACE_DIR"],
        )
        response = service.create_plan(travel_request)
        return jsonify(response.model_dump(mode="json")), 201
    except (ValidationError, SafetyError) as exc:
        details = exc.errors() if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        return jsonify(error="Invalid travel request", details=details), 422
    except Exception:
        current_app.logger.exception("Travel planning failed")
        return jsonify(error="Travel planning failed", retryable=True), 502


@travel_api_bp.get("/traces/<uuid:request_id>")
def get_trace(request_id):
    """Return sanitized decision metadata; protect this endpoint with auth in production."""
    path = current_app.config["TRACE_DIR"] / f"{request_id}.jsonl"
    if not path.is_file():
        return jsonify(error="Trace not found"), 404
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return jsonify(request_id=str(request_id), events=events)
