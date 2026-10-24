"""Flask application factory."""

from __future__ import annotations

from flask import Flask, jsonify, request
from flask_wtf.csrf import CSRFError, CSRFProtect

from flaskapp.config import Config

csrf = CSRFProtect()

# Every source this app legitimately loads, and nothing else. Built as a list so
# each directive carries the reason it is there; a bare string would lose that
# the moment someone needed to change one.
#
# Three external origins, not one. `cdn.jsdelivr.net` is the only one visible in
# the templates, but `static/css/app.css:1` opens with an `@import` of a Google
# Fonts stylesheet, which in turn fetches its woff2 files from a *fourth-party*
# host. Miss either and DM Sans / Manrope fall back to `system-ui` on every
# page — with no console error and no failing test, just a product that quietly
# looks wrong. `tests/test_security_headers.py` reads the CSS for exactly this
# reason rather than trusting this comment.
_CSP_DIRECTIVES = (
    # Everything not named below: same origin only.
    "default-src 'self'",
    # No 'unsafe-inline'. The app has no executing inline <script> and no inline
    # event handlers, so it does not need one — see the test that forbids it.
    # The two <script type="application/json"> data islands are not scripts and
    # CSP does not govern them.
    "script-src 'self' https://cdn.jsdelivr.net",
    # `@import` is matched by style-src, NOT style-src-elem, which is why
    # fonts.googleapis.com belongs here and not only in font-src.
    "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com",
    # jsdelivr serves the bootstrap-icons woff2; gstatic serves the Google Fonts
    # files that the imported stylesheet points at.
    "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com",
    # `data:` is load-bearing: Bootstrap 5.3 draws .form-select chevrons and
    # .form-check ticks as inline SVG data URIs. Without it the login form loses
    # its dropdown arrows.
    "img-src 'self' data:",
    # All API traffic is same-origin /api/v1. No fetch() to anywhere else.
    "connect-src 'self'",
    "object-src 'none'",
    # The modern spelling of the X-Frame-Options header set below. Both are
    # correct to send: browsers prefer this one, scanners look for both.
    "frame-ancestors 'none'",
    # These two and frame-ancestors do NOT fall back to default-src, so omitting
    # them would leave real gaps rather than inheriting the line above.
    "form-action 'self'",
    "base-uri 'none'",
)

CONTENT_SECURITY_POLICY = "; ".join(_CSP_DIRECTIVES)


def create_app(config_object: type[Config] = Config) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_object(config_object)
    csrf.init_app(app)

    from flaskapp.database import init_app as init_database

    init_database(app)

    from flaskapp.health import health_bp
    from flaskapp.routes import pages_bp
    from flaskapp.travel_ai.api import travel_api_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(travel_api_bp, url_prefix="/api/v1")

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        """Keep API failures JSON so browser code never parses an HTML error page."""
        if request.path.startswith("/api/"):
            return jsonify(
                error="Your session security token expired. Refresh the page and try again.",
                code="csrf_token_invalid",
            ), 400
        return error.description, 400

    @app.after_request
    def set_security_headers(response):
        """Response headers the DAST scan found missing on every page.

        Both were reported at Medium risk by the first authenticated ZAP run
        (backlog item 13). Nothing set response headers anywhere in this app
        before now, so this is the one place they belong.

        setdefault rather than assignment: a view that deliberately needs
        different framing or content-type behaviour can still say so, and this
        will not silently overwrite it.

        Content-Security-Policy was the third finding from that scan and was
        deliberately held back until it could land with its own scan run. This
        is that run. It is the one header here that can break a page rather
        than fail loudly, so the sources are derived from the templates and the
        stylesheet rather than guessed, a test asserts every external origin in
        those files is covered, and `tests/e2e/test_csp.py` loads each page in a
        real browser and fails on any CSP violation in the console. A unit test
        cannot catch a blocked inline style; only a browser can.

        No `report-uri`/`report-to`: there is nothing to receive the reports,
        and a dangling endpoint is worse than none. No
        `upgrade-insecure-requests`: the DAST scan and the e2e server both run
        plain HTTP on loopback and it would rewrite those requests to https.
        """
        # Stop the browser second-guessing a declared Content-Type. Without it
        # a file we serve as text can be sniffed and executed as script.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Refuse to be rendered inside a frame anywhere. DENY rather than
        # SAMEORIGIN because nothing in this app frames itself.
        response.headers.setdefault("X-Frame-Options", "DENY")
        # The sources are a module constant, not built here, so the policy can
        # be read and tested without starting a request.
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

    return app
