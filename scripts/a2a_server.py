"""Run the four specialist agents as an official A2A 1.x ASGI service."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import uvicorn

from flaskapp import create_app
from flaskapp.config import get_llm_settings
from flaskapp.travel_ai.guardrails import LlmGuardrail, guardrail_settings
from flaskapp.travel_ai.a2a_standard import ExecutorContext, build_a2a_application
from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES
from flaskapp.travel_ai.llm import build_llm
from flaskapp.travel_ai.safeguards import screen_request_l2, validate_request
from flaskapp.travel_ai.service import TravelPlanningService
from flaskapp.travel_ai.tracing import AuditTracer


def create_application(
    flask_app=None,
    node_factories: Mapping[str, Any] | None = None,
    orchestrator_runner=None,
):
    """Construct the ASGI sidecar using real or injected specialist nodes.

    Production callers omit ``node_factories`` and therefore retain fail-fast
    LLM configuration validation. Tests may inject deterministic nodes so CI
    does not need provider credentials merely to verify HTTP route composition.
    """
    flask_app = flask_app or create_app()
    if node_factories is None:
        llm_settings, configuration_error = get_llm_settings(flask_app.config)
        if configuration_error or llm_settings is None:
            raise RuntimeError(configuration_error or "LLM configuration is unavailable")
        llm = build_llm(**llm_settings)
        trace_dir = Path(flask_app.config["TRACE_DIR"])
        database_path = flask_app.config["DATABASE"]

        resolved_node_factories = {}
        for name, create_node in SPECIALIST_NODE_FACTORIES.items():
            def node_factory(request_id, create_node=create_node):
                tracer = AuditTracer(trace_dir, request_id, database_path)
                return create_node(llm, tracer)
            resolved_node_factories[name] = node_factory

        if orchestrator_runner is None:
            def orchestrator_runner(request, request_id):
                validated_request = validate_request(
                    request.model_dump(mode="json"),
                    flask_app.config["MAX_INPUT_CHARS"],
                )
                guard_settings = guardrail_settings(flask_app.config)
                input_verdict = screen_request_l2(
                    validated_request,
                    LlmGuardrail.from_settings(guard_settings),
                )
                service = TravelPlanningService(
                    **llm_settings,
                    trace_dir=trace_dir,
                    database_path=database_path,
                    guardrail_settings=guard_settings,
                    input_guardrail=(
                        input_verdict.as_audit_details() if input_verdict else None
                    ),
                    a2a_base_url=flask_app.config["A2A_BASE_URL"],
                )
                return service.create_plan(validated_request, request_id=request_id)
    else:
        resolved_node_factories = dict(node_factories)

    return build_a2a_application(
        resolved_node_factories,
        flask_app.config["A2A_BASE_URL"],
        orchestrator_runner=orchestrator_runner,
        context=ExecutorContext.from_flask_config(flask_app.config),
        root_agent=flask_app.config.get("A2A_ROOT_AGENT"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the travel specialist A2A server")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    flask_app = create_app()
    uvicorn.run(
        create_application(flask_app),
        host=args.host or flask_app.config["A2A_HOST"],
        port=args.port or flask_app.config["A2A_PORT"],
    )


if __name__ == "__main__":
    main()
