"""Flask application factory."""

from __future__ import annotations

from flask import Flask
from flask_wtf.csrf import CSRFProtect

from flaskapp.config import Config

csrf = CSRFProtect()


def create_app(config_object: type[Config] = Config) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_object(config_object)
    csrf.init_app(app)

    from flaskapp.database import init_app as init_database

    init_database(app)

    from flaskapp.routes import pages_bp
    from flaskapp.travel_ai.api import travel_api_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(travel_api_bp, url_prefix="/api/v1")
    return app


# Backwards-compatible export for code importing ``from flaskapp import app``.
app = create_app()
