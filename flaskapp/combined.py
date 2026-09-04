"""Composition of the Flask website and official A2A routes."""

from __future__ import annotations

from a2wsgi import WSGIMiddleware
from starlette.applications import Starlette
from starlette.routing import Mount

from flaskapp import create_app
from scripts.a2a_server import create_application as create_a2a_application


def create_application(*, base_url: str | None = None) -> Starlette:
    """Build the combined application, sharing one Flask configuration."""
    flask_app = create_app()
    if base_url:
        flask_app.config["A2A_BASE_URL"] = base_url.rstrip("/")
    a2a_app = create_a2a_application(flask_app)
    # A2A routes must precede Flask's catch-all mount.
    return Starlette(routes=[
        *a2a_app.routes,
        Mount("/", app=WSGIMiddleware(flask_app)),
    ])
