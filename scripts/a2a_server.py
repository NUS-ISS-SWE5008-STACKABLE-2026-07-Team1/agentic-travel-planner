"""Run the four specialist agents as an official A2A 1.x ASGI service."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import uvicorn

from flaskapp import create_app
from flaskapp.config import get_llm_settings
from flaskapp.travel_ai.a2a_standard import build_a2a_application
from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES
from flaskapp.travel_ai.llm import build_llm
from flaskapp.travel_ai.tracing import AuditTracer


def create_application(
    flask_app=None,
    node_factories: Mapping[str, Any] | None = None,
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
    else:
        resolved_node_factories = dict(node_factories)

    return build_a2a_application(
        resolved_node_factories, flask_app.config["A2A_BASE_URL"]
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
