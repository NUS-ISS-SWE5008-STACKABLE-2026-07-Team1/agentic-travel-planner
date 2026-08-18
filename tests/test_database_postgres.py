# tests/test_database_postgres.py
"""The production dialect, exercised for real.

Skipped without TEST_DATABASE_URL so local runs stay offline and SQLite-only.
CI sets it, so the dialect that actually ships is never untested.
"""

import os
import uuid

import pytest

from flaskapp.database import (
    connect, create_planning_job, create_user, get_admin_token_summary,
    get_platform_dashboard, get_recent_feedback, initialize, save_agent_run,
    save_plan, save_plan_feedback, update_planning_job,
)
from flaskapp.travel_ai.schemas import (
    AgentFinding, Option, PlanResponse, TravelPlan, TravelRequest,
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

    With no completed jobs, average_feedback and needs_improvement_rate are
    legitimately None by design (see get_platform_dashboard's
    `... if completed else None`). So this seeds one full request lifecycle —
    a user, a completed planning job, and a piece of feedback on it, via the
    real helper functions rather than hand-written INSERTs — and asserts on
    the values that data implies. That proves the dashboard aggregates real
    rows correctly on Postgres, not just that the queries parse.
    """
    with connect(database) as db:
        user_id = db.insert_returning_id(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Feedback Giver", f"{uuid.uuid4()}@example.com", "hash"),
        )
    request_id = str(uuid.uuid4())
    create_planning_job(database, request_id, user_id, {"origin": "Singapore"})
    update_planning_job(database, request_id, "completed")
    feedback = save_plan_feedback(database, request_id, user_id, "up", "great trip")
    assert feedback is not None, "seeding failed: feedback was not recorded"

    dashboard = get_platform_dashboard(database)
    assert isinstance(dashboard, dict)
    assert dashboard, "the dashboard returned nothing at all"

    assert dashboard["registered_users"] == 1
    assert dashboard["active_users"] == 1
    assert dashboard["total_requests"] == 1
    assert dashboard["completed_requests"] == 1
    assert dashboard["failed_requests"] == 0
    assert dashboard["completion_rate"] == 100.0
    assert dashboard["adoption_rate"] == 100.0
    assert dashboard["engagement_score"] == 20.0
    assert dashboard["feedback_count"] == 1
    assert dashboard["average_feedback"] == 100.0, "1 of 1 completed job got positive feedback"
    assert dashboard["needs_improvement_rate"] == 0.0, "no negative feedback was recorded"

    # With a completed job and feedback seeded, every aggregate is defined —
    # the None case above no longer applies to any key.
    for key, value in dashboard.items():
        assert value is not None, f"{key} came back None"


def test_save_plan_recent_feedback_and_token_summary_round_trip(database):
    """Exercises the two hardest-won Postgres fixes that
    test_dashboard_queries_run never touches, because get_platform_dashboard
    -- the only function it calls -- contains neither:

    * the `json_extract(...)` -> `(...)::json->>'field'` rewrite, which only
      get_recent_feedback uses (reading planning_jobs.request_json);
    * the numeric/Decimal FloatLoader registration in connect(), which only
      matters for a column computed with SQL ROUND()/AVG() -- Postgres
      returns those as `numeric`, and psycopg maps `numeric` to Decimal
      unless the loader is registered. get_admin_token_summary's
      agent_performance rows (completion_rate, average_tokens) are the only
      values in this module built that way.

    Also covers the save_plan round trip the spec's Testing section promised
    and no task implemented: a real TravelRequest/PlanResponse, including an
    agent finding with an option, saved and read back through the actual
    application helper rather than a hand-written INSERT.
    """
    with connect(database) as db:
        user_id = db.insert_returning_id(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Round Tripper", f"{uuid.uuid4()}@example.com", "hash"),
        )
    request_id = str(uuid.uuid4())

    request = TravelRequest(
        origin="Singapore", destination="Japan", origin_city="Singapore",
        destination_city="Tokyo", departure_date="2026-10-10",
        return_date="2026-10-16", travellers=1, traveller_ages=[30],
        traveller_genders=["prefer_not_to_say"], traveller_accessibility_needs=[[]],
        budget=3000,
    )
    response = PlanResponse(
        request_id=request_id,
        plan=TravelPlan(
            title="Japan plan", summary="A test plan", itinerary=["Day 1"],
            rationale=["Matches budget"],
        ),
        agent_findings=[AgentFinding(
            agent="flight_agent", summary="Found flights", confidence=0.9,
            options=[Option(name="SQ flight", description="Direct", estimated_cost=800)],
        )],
        trace_url="/trace",
    )
    save_plan(database, request, response, [], user_id=user_id)

    with connect(database) as db:
        saved = db.execute(
            "SELECT title FROM travel_plans WHERE request_id = ?", (request_id,)
        ).fetchone()
        option_count = db.execute(
            """SELECT COUNT(*) AS n FROM options o
               JOIN agent_findings f ON f.id = o.finding_id
               WHERE f.request_id = ?""",
            (request_id,),
        ).fetchone()["n"]
    assert saved["title"] == "Japan plan"
    assert option_count == 1, "save_plan's agent_findings/options round trip must persist"

    # get_recent_feedback needs a planning_jobs row -- its request_json is
    # exactly what json_extract/->>'field' reads -- and a 'completed'
    # status, since save_plan_feedback only accepts feedback on those.
    create_planning_job(database, request_id, user_id, {
        "origin": "Singapore", "destination": "Japan",
        "origin_city": "Singapore", "destination_city": "Tokyo",
    })
    update_planning_job(database, request_id, "completed")
    feedback = save_plan_feedback(database, request_id, user_id, "up", "loved it")
    assert feedback is not None, "seeding failed: feedback was not recorded"

    recent = get_recent_feedback(database)
    assert len(recent) == 1
    entry = recent[0]
    assert entry["rating"] == "up"
    assert entry["origin"] == "Singapore", "json_extract($.origin) must survive the rewrite"
    assert entry["destination"] == "Japan"
    assert entry["origin_city"] == "Singapore"
    assert entry["destination_city"] == "Tokyo"

    # get_admin_token_summary: ROUND(...)/AVG(...) columns.
    save_agent_run(
        database, request_id, "flight_agent", "completed",
        usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    )

    summary = get_admin_token_summary(database)
    assert summary["totals"]["total_tokens"] == 150
    performance = {row["agent"]: row for row in summary["agent_performance"]}
    completion_rate = performance["flight_agent"]["completion_rate"]
    average_tokens = performance["flight_agent"]["average_tokens"]
    assert completion_rate == 100.0
    assert average_tokens == 150.0
    # The regression this guards against: without the FloatLoader
    # registered, psycopg hands back a Decimal here, and Flask's JSON
    # provider serializes Decimal as the *string* "100.0" -- not a number
    # the admin dashboard's charts can plot.
    assert isinstance(completion_rate, float), f"got {type(completion_rate).__name__}, not float"
    assert isinstance(average_tokens, float), f"got {type(average_tokens).__name__}, not float"


def test_create_user_rejects_a_duplicate_email(database):
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
