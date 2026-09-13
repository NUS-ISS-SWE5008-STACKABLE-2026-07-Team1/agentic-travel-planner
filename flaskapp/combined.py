"""Composition of the Flask website and official A2A routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a2wsgi import WSGIMiddleware
from starlette.applications import Starlette
from starlette.routing import Mount

from flaskapp import create_app
from flaskapp.travel_ai.a2a_standard import A2A_MIDDLEWARE
from scripts.a2a_server import create_application as create_a2a_application


def create_application(
    *,
    base_url: str | None = None,
    node_factories: Mapping[str, Any] | None = None,
    orchestrator_runner=None,
) -> Starlette:
    """Build the combined application, sharing one Flask configuration."""
    flask_app = create_app()
    if base_url:
        flask_app.config["A2A_BASE_URL"] = base_url.rstrip("/")
    flask_app.config["A2A_INTERNAL_ENABLED"] = True
    a2a_app = create_a2a_application(
        flask_app,
        node_factories=node_factories,
        orchestrator_runner=orchestrator_runner,
    )
    # A2A routes must precede Flask's catch-all mount.
    #
    # `A2A_MIDDLEWARE` has to be re-applied here: taking `a2a_app.routes`
    # discards everything attached to that app, and the Agent Card cache
    # headers went missing exactly that way until the TCK reported it.
    return Starlette(
        routes=[*a2a_app.routes, Mount("/", app=WSGIMiddleware(flask_app))],
        middleware=list(A2A_MIDDLEWARE),
    )
