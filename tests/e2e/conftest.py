"""Fixtures for browser tests against a real, running Flask server.

Everything else in `tests/` calls Flask's in-process test client. Playwright
drives a real browser over real HTTP, so it needs an actual server — hence
`live_server` here instead of reusing anything from the top-level
`tests/conftest.py`.

Never `instance/travel_planner.sqlite3`: every server this file starts gets
its own throwaway SQLite file under `tmp_path_factory`, so a test run can
never touch the real dev database.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from http.server import HTTPServer

import pytest
from flask import Flask
from flask.sessions import SecureCookieSessionInterface
from werkzeug.serving import make_server

from scripts.ci_stub_provider import Handler as StubLlmHandler


@pytest.fixture(scope="session")
def stub_llm_base_url():
    """The same schema-synthesising stub `ci-dast.yml` uses for the app's LLM calls.

    Run in-process on a background thread rather than as a subprocess, on an
    OS-assigned port, so nothing here can collide with a developer's own
    `python scripts/ci_stub_provider.py` (which binds the fixed port 8081).
    """
    server = HTTPServer(("127.0.0.1", 0), StubLlmHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@dataclass(frozen=True)
class LiveServer:
    base_url: str
    app: Flask


@pytest.fixture(scope="session")
def live_server(tmp_path_factory, stub_llm_base_url) -> LiveServer:
    """A real Flask server on a background thread, config injected directly.

    Deliberately does NOT set environment variables and re-import
    `flaskapp.config` — `Config`'s settings are read from `os.environ` once,
    at first import, and by the time this fixture runs some other test module
    has almost certainly already imported it. A `Config` subclass overriding
    just the settings this needs, passed to `create_app(config_object=...)`,
    is the extension point the app factory already exposes for exactly this.
    """
    from flaskapp import create_app
    from flaskapp.config import Config

    db_path = tmp_path_factory.mktemp("e2e-db") / "travel_planner.sqlite3"
    trace_dir = tmp_path_factory.mktemp("e2e-traces")

    class E2EConfig(Config):
        DATABASE = db_path
        TRACE_DIR = trace_dir
        SECRET_KEY = "e2e-test-secret-key"  # nosec - throwaway, per-run only
        # The test server runs over plain http, so a Secure-flagged cookie
        # never round-trips back — same reasoning as ci-dast.yml.
        SESSION_COOKIE_SECURE = False
        # Same stub-provider wiring as ci-dast.yml: an OpenAI-compatible
        # endpoint, never a real provider key, so a plan can actually run to
        # completion here without any live model call or spend.
        LLM_PROVIDER = "openai_compatible"
        LLM_API_KEY = "stub-not-a-real-key"
        LLM_BASE_URL = stub_llm_base_url
        LLM_MODEL = "stub-model"

    app = create_app(config_object=E2EConfig)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield LiveServer(base_url=f"http://127.0.0.1:{server.server_port}", app=app)
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _demo_user_row(live_server: LiveServer) -> sqlite3.Row:
    """The demo account every fresh database gets for free.

    `flaskapp/database.py`'s `init_app` calls `seed_login_user()` on every
    startup (see `LOGIN_EMAIL`/`LOGIN_PASSWORD_HASH` in `Config`), so this
    account exists in `live_server`'s throwaway database with no setup here.
    """
    connection = sqlite3.connect(str(live_server.app.config["DATABASE"]))
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT id, email, name, country FROM users WHERE email = ?",
            (live_server.app.config["LOGIN_EMAIL"],),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("Demo login user was not seeded into the e2e database")
    return row


@pytest.fixture()
def demo_user_session_cookie(live_server: LiveServer) -> str:
    """A signed session cookie for the demo user, for tests that don't need to
    exercise the login page itself (see docs on the login problem: option 3,
    the fast default for anything that just needs to *be* authenticated).
    """
    row = _demo_user_row(live_server)
    serializer = SecureCookieSessionInterface().get_signing_serializer(live_server.app)
    return serializer.dumps({
        "authenticated": True,
        "user_id": row["id"],
        "user_email": row["email"],
        "user_name": row["name"] or "Traveller",
        "user_country": row["country"] or "",
    })


@pytest.fixture()
def authenticated_context(live_server: LiveServer, demo_user_session_cookie: str, context):
    """`pytest-playwright`'s per-test `context`, pre-loaded with a valid session.

    Cookie injection needs no CSRF workaround: Flask-WTF's token is bound to
    the session and regenerated by the real page JS on every load, regardless
    of how that session was established.
    """
    context.add_cookies([{
        "name": "session",
        "value": demo_user_session_cookie,
        "url": live_server.base_url,
    }])
    return context
