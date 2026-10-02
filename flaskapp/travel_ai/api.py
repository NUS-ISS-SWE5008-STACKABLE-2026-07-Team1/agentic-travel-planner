"""Versioned HTTP API for travel planning and audit retrieval."""

from __future__ import annotations

import json
import re
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, session
from pydantic import ValidationError
from werkzeug.security import generate_password_hash

from flaskapp.travel_ai.safeguards import (
    GuardrailBlocked, SafetyError, screen_answers, screen_prompt, screen_request_l2,
    validate_request,
)
from flaskapp.travel_ai.guardrails import (
    GUARDRAIL_PROMPT_VERSION, Category, LlmGuardrail, build_guardrail, guardrail_settings,
)
from flaskapp.travel_ai.guardrails.pii import PiiRedactor
from flaskapp.travel_ai.jobs import cancel_job, get_job, submit_plan
from flaskapp.travel_ai.llm import build_llm
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.agents.orchestrator_agent.intake import (
    clarification_question, compute_gaps, extract_intent, merge_answers,
    merge_intents, to_request_payload,
)
from flaskapp.travel_ai.agents.orchestrator_agent.intake_schemas import (
    ExtractedIntent, IntentResponse,
)
from flaskapp.database import (
    get_admin_activity, get_admin_token_summary, get_admins, get_platform_dashboard,
    end_user_request_session, get_recent_feedback, get_system_logs,
    owns_intake_request, owns_request, register_admin, save_intake_request,
    save_intake_message, save_plan_feedback,
)
from flaskapp.admin_auth import is_admin_email
from flaskapp.config import get_llm_settings

travel_api_bp = Blueprint("travel_api", __name__)


def validation_details(exc: ValidationError) -> list[dict]:
    """JSON-safe validation errors.

    `ValidationError.errors()` keeps the original exception object in
    `ctx["error"]` for `model_validator` failures, so passing it to `jsonify`
    raises TypeError and the caller receives Flask's HTML error page instead of
    the 422 it is waiting for. `.json()` renders the same errors as text.
    """
    return json.loads(exc.json())


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
        intake_request_id = str(payload.pop("_request_id", "")).strip() or None
        if intake_request_id and not owns_intake_request(
            current_app.config["DATABASE"], intake_request_id, session.get("user_id")
        ):
            return jsonify(error="Intake request not found"), 404
        travel_request = validate_request(payload, current_app.config["MAX_INPUT_CHARS"])
        # L2 runs here rather than inside the background job so a rejected
        # traveller gets an immediate 422 instead of a job that fails ~75s
        # later. It costs the POST one small-model round trip; the plan it
        # guards costs five large ones.
        guard_settings = guardrail_settings(current_app.config)
        input_verdict = screen_request_l2(
            travel_request, LlmGuardrail.from_settings(guard_settings)
        )
        settings = {
            **llm_settings,
            "trace_dir": str(current_app.config["TRACE_DIR"]),
            "database_path": str(current_app.config["DATABASE"]),
            # Carried into the background thread so the orchestrator's output
            # gate uses the same configuration the input gate just used.
            "guardrail": guard_settings,
            "input_guardrail": input_verdict.as_audit_details() if input_verdict else None,
            "a2a_base_url": (
                current_app.config.get("A2A_BASE_URL")
                if current_app.config.get("A2A_INTERNAL_ENABLED", False)
                else None
            ),
            # The flight-only transport seam. Independent of the setting above:
            # that one routes every specialist over A2A, this one just the
            # flight agent (ADR-0004).
            "flight_agent_transport": current_app.config.get("FLIGHT_AGENT_TRANSPORT"),
            "flight_agent_a2a_url": current_app.config.get("FLIGHT_AGENT_A2A_URL"),
        }
        job = (
            submit_plan(travel_request, settings, session.get("user_id"), intake_request_id)
            if intake_request_id
            else submit_plan(travel_request, settings, session.get("user_id"))
        )
        return jsonify(
            request_id=job.request_id,
            status=job.status,
            chat_url=f"/chat/{job.request_id}",
        ), 202
    except GuardrailBlocked as exc:
        # No AuditTracer exists yet — the request has no id and no job — so the
        # application log is the only place this decision can be recorded.
        # Enums and numbers only, never the traveller's text.
        current_app.logger.warning("L2 input guardrail blocked: %s", exc.verdict.as_audit_details())
        return jsonify(error="Invalid travel request", details=[{"msg": str(exc)}]), 422
    except (ValidationError, SafetyError) as exc:
        details = validation_details(exc) if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        return jsonify(error="Invalid travel request", details=details), 422
    except Exception:
        current_app.logger.exception("Travel planning failed")
        return jsonify(error="Travel planning failed", retryable=True), 502


@travel_api_bp.post("/travel-intents")
def create_travel_intent():
    """Read a free-text trip request and report what is still missing."""
    llm_settings, configuration_error = get_llm_settings(current_app.config)
    if configuration_error:
        return jsonify(error=configuration_error), 503
    payload = request.get_json(silent=True) or {}
    try:
        screened = screen_prompt(
            payload.get("prompt"), current_app.config["MAX_INPUT_CHARS"],
            build_guardrail(current_app.config),
            PiiRedactor.from_config(current_app.config),
        )
        # From here on the raw prompt is out of scope by construction. The
        # redacted string is what gets persisted, what the model sees, and what
        # the traveller reads back; these are the two lines that make the
        # redaction layer more than detection.
        prompt = screened.text
        current = ExtractedIntent.model_validate(payload.get("extracted") or {})
        is_first_turn = not payload.get("request_id")
        intake_request_id = str(payload.get("request_id") or uuid4())
        if not save_intake_request(
            current_app.config["DATABASE"], intake_request_id, session.get("user_id"),
            current.model_dump(mode="json"),
        ):
            return jsonify(error="Intake request not found"), 404
        save_intake_message(
            current_app.config["DATABASE"], intake_request_id, "user", prompt
        )
        extraction = extract_intent(build_llm(**llm_settings), prompt)
        intent = merge_intents(current, extraction.intent)
    except GuardrailBlocked as exc:
        current_app.logger.warning("L2 intake guardrail blocked: %s", exc.verdict.as_audit_details())
        return jsonify(error=str(exc)), 422
    except SafetyError as exc:
        return jsonify(error=str(exc)), 422
    except ValidationError as exc:
        return jsonify(error="The assistant could not read that request. Try the detailed form.",
                       details=validation_details(exc)), 422
    except Exception:
        current_app.logger.exception("Travel intent extraction failed")
        return jsonify(error="The assistant is unavailable. Please retry.", retryable=True), 502
    if not save_intake_request(
        current_app.config["DATABASE"], intake_request_id, session.get("user_id"),
        intent.model_dump(mode="json"),
    ):
        return jsonify(error="Intake request not found"), 404
    response_body = _intent_response(intent, extraction.question, intake_request_id)
    save_intake_message(
        current_app.config["DATABASE"], intake_request_id, "assistant",
        response_body["question"],
    )
    tracer = AuditTracer(
        current_app.config["TRACE_DIR"], intake_request_id, current_app.config["DATABASE"]
    )
    if is_first_turn:
        tracer.record("user_request_submitted", "system", {"stage": "intake"})
    if screened.pii.redacted:
        # Rule names and counts only. This event is served by
        # `GET /api/v1/traces/<id>` and rendered in the admin dashboard, so
        # recording *what* was redacted would put the identifier straight back
        # into the audit log the redaction exists to keep it out of.
        tracer.record("pii_redacted", "orchestrator_agent", screened.pii.as_audit_details())
    tracer.record(
        "orchestrator_validation_started", "orchestrator_agent",
        {"turn": "initial" if is_first_turn else "clarification"},
    )
    tracer.record(
        "orchestrator_validation_completed", "orchestrator_agent",
        {
            "complete": response_body["complete"],
            "missing_count": len(response_body["missing"]),
            "next_stage": "specialist_planning" if response_body["complete"] else "clarification",
        },
    )
    return jsonify(response_body)


@travel_api_bp.post("/travel-intents/resolve")
def resolve_travel_intent():
    """Apply the traveller's answers. Deterministic: no model call happens here."""
    payload = request.get_json(silent=True) or {}
    try:
        answers = screen_answers(payload.get("answers") or {})
        extracted = ExtractedIntent.model_validate(payload.get("extracted") or {})
        extracted = merge_answers(extracted, answers)
    except SafetyError as exc:
        return jsonify(error=str(exc)), 422
    except ValidationError as exc:
        return jsonify(error="Some answers could not be used", details=validation_details(exc)), 422
    return jsonify(_intent_response(extracted, str(payload.get("question") or "")))


def _intent_response(extracted, question: str, request_id: str | None = None):
    """Shared reply shape for both intake endpoints."""
    missing = compute_gaps(extracted)
    complete = not missing
    return IntentResponse(
        complete=complete,
        question=(f"{question} {clarification_question(missing)}" if missing
                  else clarification_question(missing)),
        extracted=extracted,
        missing=missing,
        request=to_request_payload(extracted) if complete else None,
        request_id=request_id,
    ).model_dump(mode="json")


# Error types worth naming to the traveller; anything else is generic.
_JOB_ERROR_MESSAGES = {
    "APITimeoutError": "Azure took too long to respond. Please retry the plan.",
    "WorkerLost": "The server running this plan restarted. Please retry the plan.",
}


@travel_api_bp.get("/travel-plans/<uuid:request_id>/status")
def get_travel_plan_status(request_id):
    # Read from the database, not this pod's memory, so any web pod can
    # answer for a plan running on another (jobs.py).
    job = get_job(current_app.config["DATABASE"], str(request_id), session.get("user_id"))
    if job is None:
        return jsonify(error="Planning job not found"), 404
    body = {"request_id": job.request_id, "status": job.status}
    if job.response is not None:
        body["response"] = job.response
    if job.error:
        body["error"] = _JOB_ERROR_MESSAGES.get(job.error, "Travel planning failed")
    return jsonify(body)


@travel_api_bp.post("/travel-plans/<uuid:request_id>/cancel")
def cancel_travel_plan(request_id):
    job = cancel_job(current_app.config["DATABASE"], str(request_id), session.get("user_id"))
    if job is None:
        return jsonify(error="Planning job not found"), 404
    return jsonify(request_id=job.request_id, status=job.status)


@travel_api_bp.post("/user-requests/<uuid:request_id>/session/end")
def end_user_request(request_id):
    if not end_user_request_session(
        current_app.config["DATABASE"], str(request_id), session.get("user_id")
    ):
        return jsonify(error="User request not found"), 404
    return jsonify(request_id=str(request_id), session_status="ended")


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
    from flaskapp.travel_ai.guardrails.pii import DEFAULT_RULES
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
        {"agent": "PII redaction (L1)", "instruction":
         "Runs on the intake prompt after the injection check and BEFORE the L2 "
         "classifier, so no identifier a traveller types reaches a model provider. "
         "The redacted text is what is stored, sent and echoed back. This layer "
         "never denies a request \u2014 it removes the identifier and planning "
         "continues. Rules, in the order applied: "
         + ", ".join(f"{rule.name} ({rule.strategy})" for rule in DEFAULT_RULES)
         + f". Enabled: {current_app.config.get('PII_REDACTION_ENABLED', True)}."},
        {"agent": "LLM guardrail classifier (L2)", "instruction":
         f"Prompt version {GUARDRAIL_PROMPT_VERSION}. Runs after the deterministic gates on "
         f"traveller free text and on the synthesized plan. Blocks at confidence >= "
         f"{current_app.config.get('GUARDRAIL_BLOCK_THRESHOLD', 0.7)}; a lower-confidence block is "
         f"recorded as a flag without denying the request. Fail mode: "
         f"{current_app.config.get('GUARDRAIL_FAIL_MODE', 'closed')}. Categories: "
         + ", ".join(item.value for item in Category if item is not Category.NONE)},
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
