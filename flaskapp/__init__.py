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

    from flaskapp.database import init_app as init_database, seed_risk_reference_data

    init_database(app)

    # Risk & Advisory's reference data is declarative, not user-writable, so
    # re-seeding it on every startup keeps the database an exact mirror of
    # seed_data.py rather than something that can drift from it. Deliberately
    # not inside database.py itself: that module knows how to persist data,
    # not which agent's data belongs in it — that decision lives here, where
    # the app is already wiring its pieces together.
    from flaskapp.travel_ai.agents.risk_advisory_agent.seed_data import (
        DATED_EVENTS, SEASONAL_WINDOWS, STANDING_FACTS,
    )

    seed_risk_reference_data(app.config["DATABASE"], STANDING_FACTS, SEASONAL_WINDOWS, DATED_EVENTS)

    from flaskapp.health import health_bp
    from flaskapp.routes import pages_bp
    from flaskapp.travel_ai.api import travel_api_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(travel_api_bp, url_prefix="/api/v1")

    @app.after_request
    def set_security_headers(response):
        """Response headers the DAST scan found missing on every page.

        Both were reported at Medium risk by the first authenticated ZAP run
        (backlog item 13). Nothing set response headers anywhere in this app
        before now, so this is the one place they belong.

        setdefault rather than assignment: a view that deliberately needs
        different framing or content-type behaviour can still say so, and this
        will not silently overwrite it.

        Content-Security-Policy is the third finding from that scan and is NOT
        here on purpose. It needs a policy listing every allowed script and
        style source, and getting it wrong blanks a page rather than failing
        loudly. It is tracked separately so it can land with its own scan run.
        """
        # Stop the browser second-guessing a declared Content-Type. Without it
        # a file we serve as text can be sniffed and executed as script.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Refuse to be rendered inside a frame anywhere. DENY rather than
        # SAMEORIGIN because nothing in this app frames itself.
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    return app
