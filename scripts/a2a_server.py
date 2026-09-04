"""Run the four specialist agents as an official A2A 1.x ASGI service."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from flaskapp import create_app
from flaskapp.config import get_llm_settings
from flaskapp.travel_ai.a2a_standard import build_a2a_application
from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES
from flaskapp.travel_ai.llm import build_llm
from flaskapp.travel_ai.tracing import AuditTracer


def create_application():
    """Construct the ASGI sidecar using the same configuration as Flask."""
    flask_app = create_app()
    llm_settings, configuration_error = get_llm_settings(flask_app.config)
    if configuration_error or llm_settings is None:
        raise RuntimeError(configuration_error or "LLM configuration is unavailable")
    llm = build_llm(**llm_settings)
    trace_dir = Path(flask_app.config["TRACE_DIR"])
    database_path = flask_app.config["DATABASE"]

    node_factories = {}
    for name, create_node in SPECIALIST_NODE_FACTORIES.items():
        def node_factory(request_id, create_node=create_node):
            tracer = AuditTracer(trace_dir, request_id, database_path)
            return create_node(llm, tracer)
        node_factories[name] = node_factory

    return build_a2a_application(node_factories, flask_app.config["A2A_BASE_URL"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the travel specialist A2A server")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    flask_app = create_app()
    uvicorn.run(
        create_application(),
        host=args.host or flask_app.config["A2A_HOST"],
        port=args.port or flask_app.config["A2A_PORT"],
    )


if __name__ == "__main__":
    main()
