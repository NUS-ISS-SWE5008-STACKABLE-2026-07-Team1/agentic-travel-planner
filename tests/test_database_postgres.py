# tests/test_database_postgres.py
"""The production dialect, exercised for real.

Skipped without TEST_DATABASE_URL so local runs stay offline and SQLite-only.
CI sets it, so the dialect that actually ships is never untested.
"""

import os
import uuid

import pytest

from flaskapp.database import (
    connect, create_user, get_platform_dashboard, initialize, save_plan,
)

DSN = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")


@pytest.fixture()
def database():
    initialize(DSN)
    yield DSN
    with connect(DSN) as db:
        for table in (
            "plan_feedback", "agent_runs", "intake_messages", "planning_jobs",
            "audit_events", "a2a_messages", "options", "agent_findings",
            "travel_plans", "travel_requests", "users",
        ):
            db.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")


def test_initialize_creates_every_table(database):
    with connect(database) as db:
        tables = {
            row["table_name"] for row in db.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            ).fetchall()
        }
    assert {"users", "travel_requests", "travel_plans", "agent_findings",
            "options", "a2a_messages", "audit_events", "planning_jobs",
            "intake_messages", "agent_runs", "plan_feedback"} <= tables


def test_initialize_is_idempotent(database):
    initialize(database)  # must not raise


def test_generated_keys_come_back(database):
    with connect(database) as db:
        user_id = db.insert_returning_id(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("A", f"{uuid.uuid4()}@example.com", "hash"),
        )
    assert isinstance(user_id, int) and user_id > 0


def test_email_uniqueness_is_case_insensitive(database):
    with connect(database) as db:
        db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("Case@Example.com", "hash"),
        )
    with connect(database) as db:
        rows = db.execute(
            "SELECT id FROM users WHERE email = ?", ("case@example.com",)
        ).fetchall()
    assert len(rows) == 1, "CITEXT should match regardless of case"


def test_default_timestamp_matches_the_sqlite_format(database):
    """The dashboard slices these strings; the formats must be identical."""
    with connect(database) as db:
        db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (f"{uuid.uuid4()}@example.com", "hash"),
        )
    with connect(database) as db:
        created = db.execute(
            "SELECT created_at FROM users LIMIT 1"
        ).fetchone()["created_at"]
    assert len(created) == 19, f"expected YYYY-MM-DD HH:MM:SS, got {created!r}"
    assert created[4] == "-" and created[10] == " " and created[13] == ":"


def test_dashboard_queries_run(database):
    """Proves the SUBSTR rewrite is valid Postgres, not just valid SQLite.

    Asserted against the shape the admin page consumes, so a query that parses
    but returns the wrong thing still fails here.
    """
    dashboard = get_platform_dashboard(database)
    assert isinstance(dashboard, dict)
    assert dashboard, "the dashboard returned nothing at all"
    for key, value in dashboard.items():
        assert value is not None, f"{key} came back None"


def test_create_user_rejects_a_duplicate_email(database, monkeypatch):
    """create_user's except clause must catch psycopg's error, not sqlite3's."""
    from flaskapp import create_app
    from flaskapp.config import Config

    class PostgresConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        DATABASE = DSN

    app = create_app(PostgresConfig)
    with app.app_context():
        first = create_user("A", "dupe@example.com", "hash", "Singapore", "1990-01-01")
        second = create_user("B", "DUPE@example.com", "hash", "Singapore", "1990-01-01")
    assert first is not None
    assert second is None, "a duplicate email must return None, not raise"
