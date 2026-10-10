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
from flaskapp.travel_ai.usage import TokenUsageCallback
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.agents.orchestrator_agent.intake import (
    clarification_question, compute_gaps, extract_intent, merge_answers,
    drop_impossible_dates, merge_intents, resolve_place_countries, to_request_payload,
)
from flaskapp.travel_ai.agents.orchestrator_agent.intake_schemas import (
    ExtractedIntent, IntentResponse,
)
from flaskapp.database import (
    add_application_token_usage,
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
    application_usage = TokenUsageCallback()
    usage_request_id = None
    try:
        payload = request.get_json(force=False, silent=False)
        if not isinstance(payload, dict):
            return jsonify(error="A JSON object is required"), 400
        intake_request_id = str(payload.pop("_request_id", "")).strip() or None
        if intake_request_id and not owns_intake_request(
            current_app.config["DATABASE"], intake_request_id, session.get("user_id")
        ):
            return jsonify(error="Intake request not found"), 404
        # An intake row already exists, so even a later L2 block can attribute
        # the classifier spend to the request the traveller can see in admin.
        usage_request_id = intake_request_id
        travel_request = validate_request(payload, current_app.config["MAX_INPUT_CHARS"])
        # L2 runs here rather than inside the background job so a rejected
        # traveller gets an immediate 422 instead of a job that fails ~75s
        # later. It costs the POST one small-model round trip; the plan it
        # guards costs five large ones.
        guard_settings = guardrail_settings(current_app.config)
        input_verdict = screen_request_l2(
            travel_request,
            LlmGuardrail.from_settings(
                guard_settings, callbacks=[application_usage]
            ),
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
        usage_request_id = job.request_id
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
    finally:
        if usage_request_id:
            add_application_token_usage(
                current_app.config["DATABASE"], usage_request_id,
                application_usage.as_dict(),
            )


@travel_api_bp.post("/travel-intents")
def create_travel_intent():
    """Read a free-text trip request and report what is still missing."""
    llm_settings, configuration_error = get_llm_settings(current_app.config)
    if configuration_error:
        return jsonify(error=configuration_error), 503
    payload = request.get_json(silent=True) or {}
    application_usage = TokenUsageCallback()
    usage_request_id = None
    try:
        screened = screen_prompt(
            payload.get("prompt"), current_app.config["MAX_INPUT_CHARS"],
            build_guardrail(current_app.config, callbacks=[application_usage]),
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
        usage_request_id = intake_request_id
        save_intake_message(
            current_app.config["DATABASE"], intake_request_id, "user", prompt
        )
        extraction = extract_intent(
            build_llm(**llm_settings), prompt, callbacks=[application_usage]
        )
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
    finally:
        if usage_request_id:
            add_application_token_usage(
                current_app.config["DATABASE"], usage_request_id,
                application_usage.as_dict(),
            )
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
    # Derive here rather than before saving, so the stored intent stays the
    # record of what the traveller actually said. This is the one place that
    # both asks the questions and builds the final payload, so a country
    # derived here reaches the card, the gap list and `TravelRequest` alike.
    extracted = resolve_place_countries(extracted)
    # A date the model invented is refused here for the same reason: this is
    # the one place that both asks the questions and builds the payload, so a
    # dropped date becomes a question instead of a trip in the past.
    extracted = drop_impossible_dates(extracted)
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
    from flaskapp.travel_ai.agents.flight_agent.prompt import (
        FLIGHT_AGENT_SYSTEM_PROMPT, FLIGHT_AGENT_TOOL_LOOP_PROMPT,
        INSTRUCTION as flight, PATH2_INSTRUCTION as flight_fallback,
    )
    from flaskapp.travel_ai.agents.hotel_transport_agent.prompt import (
        HOTEL_TRANSPORT_SYSTEM_PROMPT, INSTRUCTION as hotel,
        PATH2_INSTRUCTION as hotel_fallback,
    )
    from flaskapp.travel_ai.agents.orchestrator_agent.intake_prompt import INTAKE_INSTRUCTION
    from flaskapp.travel_ai.agents.orchestrator_agent.prompt import INSTRUCTION as orchestrator
    from flaskapp.travel_ai.agents.risk_advisory_agent.prompt import (
        INSTRUCTION as risk, RISK_ADVISORY_SYSTEM_PROMPT,
    )
    from flaskapp.travel_ai.agents.shared import SYSTEM_POLICY
    from flaskapp.travel_ai.guardrails.pii import DEFAULT_RULES
    from flaskapp.travel_ai.guardrails.prompts import (
        INPUT_CLASSIFIER_SYSTEM, OUTPUT_CLASSIFIER_SYSTEM,
    )
    from flaskapp.travel_ai.safeguards import PROMPT_INJECTION, SENSITIVE_KEYS
    items = [
        {"agent": "Shared agent system policy", "instruction": SYSTEM_POLICY,
         "kind": "Prompt", "owner": "All agents"},
        {"agent": "Flight agent", "instruction": flight},
        {"agent": "Flight grounded reasoning prompt", "instruction": FLIGHT_AGENT_SYSTEM_PROMPT,
         "kind": "Execution prompt", "owner": "Flight agent"},
        {"agent": "Flight fallback prompt", "instruction": flight_fallback,
         "kind": "Execution prompt", "owner": "Flight agent"},
        {"agent": "Flight tool-loop prompt", "instruction": FLIGHT_AGENT_TOOL_LOOP_PROMPT,
         "kind": "Execution prompt", "owner": "Flight agent"},
        {"agent": "Hotel & transport agent", "instruction": hotel},
        {"agent": "Hotel & transport grounded reasoning prompt",
         "instruction": HOTEL_TRANSPORT_SYSTEM_PROMPT,
         "kind": "Execution prompt", "owner": "Hotel & transport agent"},
        {"agent": "Hotel & transport fallback prompt", "instruction": hotel_fallback,
         "kind": "Execution prompt", "owner": "Hotel & transport agent"},
        {"agent": "Accessibility agent", "instruction": accessibility},
        {"agent": "Risk & advisory agent", "instruction": risk},
        {"agent": "Risk & advisory grounded reasoning prompt",
         "instruction": RISK_ADVISORY_SYSTEM_PROMPT,
         "kind": "Execution prompt", "owner": "Risk & advisory agent"},
        {"agent": "Orchestrator agent", "instruction": orchestrator},
        {"agent": "Orchestrator conversational intake prompt", "instruction": INTAKE_INSTRUCTION,
         "kind": "Execution prompt", "owner": "Orchestrator agent"},
        {"agent": "L2 input-classifier prompt", "instruction": INPUT_CLASSIFIER_SYSTEM,
         "kind": "Guardrail prompt", "owner": "Application"},
        {"agent": "L2 output-classifier prompt", "instruction": OUTPUT_CLASSIFIER_SYSTEM,
         "kind": "Guardrail prompt", "owner": "Application"},
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
        {"agent": "Flight agent guardrails", "instruction":
         "Before the model: block prompt injection, high-risk stereotyping and toxicity. "
         "After the model: screen bias and toxicity, reject flight IDs absent from retrieved "
         "inventory, and retry or fall back when grounding fails. Tool search envelopes, "
         "airport/date limits and loop budgets are enforced deterministically.",
         "kind": "Guardrail", "owner": "Flight agent"},
        {"agent": "Hotel & transport agent guardrails", "instruction":
         "Before the model: block prompt injection, high-risk stereotyping and toxicity. "
         "After the model: screen generated rationale and reject hotel IDs absent from the "
         "deterministic proposal. Accessibility remains a hard constraint, and unavailable "
         "inventory uses a non-specific fallback.",
         "kind": "Guardrail", "owner": "Hotel & transport agent"},
        {"agent": "Risk & advisory agent guardrails", "instruction":
         "Screen input and output for injection, stereotyping and toxicity. Reject risk IDs "
         "absent from retrieved reference data. Deterministically require escalation for "
         "high-severity risks and prevent fabricated live visa, border or regulatory facts.",
         "kind": "Guardrail", "owner": "Risk & advisory agent"},
        {"agent": "Accessibility agent guardrails", "instruction":
         "Screen traveller input, peer-agent candidates and retrieved excerpts before model "
         "use. Screen output for injection, harmful generalization and toxicity. Enforce exact "
         "evidence IDs and retrieved URLs, evidence status markers, bounded ratings, freshness, "
         "supplier questions and VETO notices for unmet critical requirements.",
         "kind": "Guardrail", "owner": "Accessibility agent"},
        {"agent": "Orchestrator agent guardrails", "instruction":
         "Remove peer options named by Accessibility VETO notices before synthesis. Screen the "
         "complete traveller-facing plan with the L2 output classifier and a fresh per-request "
         "canary leak detector. Retry once, then withhold unsafe output. Preserve risk "
         "escalation, uncertainty, provenance and deterministic safety assessment. Per-request "
         "canary values are never displayed.",
         "kind": "Guardrail", "owner": "Orchestrator agent"},
    ]
    owners = {
        "Flight agent": "Flight agent",
        "Hotel & transport agent": "Hotel & transport agent",
        "Accessibility agent": "Accessibility agent",
        "Risk & advisory agent": "Risk & advisory agent",
        "Orchestrator agent": "Orchestrator agent",
    }
    kinds = {
        "Flight agent": "Agent-card prompt",
        "Hotel & transport agent": "Agent-card prompt",
        "Accessibility agent": "Execution prompt",
        "Risk & advisory agent": "Execution prompt",
        "Orchestrator agent": "Execution prompt",
        "PII redaction (L1)": "Guardrail",
    }
    for item in items:
        item.setdefault(
            "kind", kinds.get(
                item["agent"],
                "Guardrail" if "guardrail" in item["agent"].lower() else "Prompt",
            ),
        )
        item.setdefault("owner", owners.get(item["agent"], "Application"))
    return items


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
