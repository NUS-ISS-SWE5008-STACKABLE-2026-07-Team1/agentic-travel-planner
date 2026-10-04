# tests/test_database_postgres.py
"""The production dialect, exercised for real.

Skipped without TEST_DATABASE_URL so local runs stay offline and SQLite-only.
CI sets it, so the dialect that actually ships is never untested.
"""

import os
import re
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


def test_job_state_shared_between_pods_round_trips(database):
    """jobs.py's cross-pod transitions, on the dialect that ships.

    Conditional UPDATEs must report rowcount correctly, and the heartbeat must
    keep sub-second precision (REAL would be float4 on Postgres, which cannot
    even hold today's epoch to the second).
    """
    from flaskapp.database import (
        cancel_planning_job, fail_lost_planning_job, finish_planning_job,
        get_planning_job, heartbeat_planning_jobs, start_planning_job,
    )

    with connect(database) as db:
        owner = db.insert_returning_id(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (f"{uuid.uuid4()}@example.com", "hash"),
        )
    now = 1_790_000_000.25
    create_planning_job(database, "job-a", owner, {"x": 1}, worker="web-1", heartbeat_at=now)
    assert start_planning_job(database, "job-a")
    assert not start_planning_job(database, "job-a"), "already processing"
    assert get_planning_job(database, "job-a")["heartbeat_at"] == now

    assert cancel_planning_job(database, "job-a", owner) == "cancelled"
    assert heartbeat_planning_jobs(database, ["job-a"], now + 10) == {"job-a"}
    assert not finish_planning_job(database, "job-a", "completed", response={"y": 2})
    assert get_planning_job(database, "job-a")["status"] == "cancelled"

    create_planning_job(database, "job-b", owner, {"x": 1}, heartbeat_at=now)
    assert not fail_lost_planning_job(database, "job-b", stale_before=now - 1)
    assert fail_lost_planning_job(database, "job-b", stale_before=now + 1)
    row = get_planning_job(database, "job-b")
    assert (row["status"], row["error_type"]) == ("failed", "WorkerLost")

    create_planning_job(database, "job-c", owner, {"x": 1}, heartbeat_at=now)
    assert finish_planning_job(database, "job-c", "completed", response={"y": 2})
    import json
    assert json.loads(get_planning_job(database, "job-c")["response_json"]) == {"y": 2}


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


def test_a_legacy_flight_eval_runs_fk_is_repointed_at_planning_jobs(database):
    """The Postgres half of `_repoint_flight_eval_run_fk`, which SQLite never runs.

    A table created under the old FK (onto travel_requests, which save_plan only
    writes after the whole graph) rejected every live eval-row insert with
    ForeignKeyViolation. `CREATE TABLE IF NOT EXISTS` does not touch it, so
    `initialize` must find that constraint in `pg_constraint`, swap it, and null
    only ids no planning job backs. Every later test's `initialize` restores the
    new FK even if this one fails midway.
    """
    from flaskapp.database import SCHEMA_POSTGRES, ensure_planning_job, save_flight_eval_run

    create_table = re.search(
        r"CREATE TABLE IF NOT EXISTS flight_agent_eval_runs \(.*?\n\);", SCHEMA_POSTGRES, re.DOTALL
    ).group(0)
    legacy_table = create_table.replace(
        "request_id TEXT REFERENCES planning_jobs(request_id) ON DELETE SET NULL",
        "request_id TEXT REFERENCES travel_requests(id) ON DELETE SET NULL",
    )
    assert legacy_table != create_table, "fixture must actually restore the old FK"

    backed, orphan = str(uuid.uuid4()), str(uuid.uuid4())
    with connect(database) as db:
        db.execute("DROP TABLE flight_agent_eval_runs")
        db.executescript(legacy_table)
        # Legal under the old FK only with a travel_requests row behind it.
        for request_id in (backed, orphan):
            db.execute(
                "INSERT INTO travel_requests (id, destination, departure_date, return_date, "
                "travellers, currency, risk_tolerance) "
                "VALUES (?, 'Japan', '2026-10-10', '2026-10-16', 1, 'SGD', 'medium')",
                (request_id,),
            )
            db.execute(
                "INSERT INTO flight_agent_eval_runs (run_type, request_id, flight_agent_mode, "
                "inventory_source, latency_ms, outcome, trace_id) "
                "VALUES ('production', ?, 'auto', 'seed', 5, 'success', ?)",
                (request_id, request_id),
            )
    ensure_planning_job(database, backed, None, {})

    initialize(database)
    initialize(database)  # and idempotent once migrated

    with connect(database) as db:
        parents = [row["parent"] for row in db.execute(
            """SELECT parent.relname AS parent
               FROM pg_constraint con
               JOIN pg_class child ON child.oid = con.conrelid
               JOIN pg_namespace nsp ON nsp.oid = child.relnamespace
               JOIN pg_class parent ON parent.oid = con.confrelid
               WHERE con.contype = 'f' AND nsp.nspname = current_schema()
                 AND child.relname = 'flight_agent_eval_runs'"""
        ).fetchall()]
        request_ids = {row["trace_id"]: row["request_id"] for row in db.execute(
            "SELECT trace_id, request_id FROM flight_agent_eval_runs"
        ).fetchall()}
    assert parents == ["planning_jobs"]
    assert request_ids == {backed: backed, orphan: None}

    # The live ordering: a planning job exists, travel_requests does not yet.
    mid_plan = str(uuid.uuid4())
    ensure_planning_job(database, mid_plan, None, {})
    save_flight_eval_run(
        database, run_type="production", request_id=mid_plan,
        flight_agent_mode="auto", inventory_source="seed", outcome="success", latency_ms=1,
    )


def test_trace_columns_are_added_and_backfilled_on_an_old_postgres_database(database):
    """The same upgrade Render and GKE run on Supabase at their next start.

    Postgres can drop a column that carries a REFERENCES clause, so the old
    shape is made by dropping the three columns; that also drops the index.
    Every later test's `initialize` restores them even if this one fails midway.
    """
    planned, unplanned = str(uuid.uuid4()), str(uuid.uuid4())
    with connect(database) as db:
        db.execute("ALTER TABLE options DROP COLUMN request_id")
        db.execute("ALTER TABLE options DROP COLUMN created_at")
        db.execute("ALTER TABLE agent_findings DROP COLUMN created_at")
        for request_id in (planned, unplanned):
            db.execute(
                "INSERT INTO travel_requests (id, destination, departure_date, return_date, "
                "travellers, currency, risk_tolerance) "
                "VALUES (?, 'Japan', '2026-10-10', '2026-10-16', 1, 'SGD', 'medium')",
                (request_id,),
            )
        db.execute(
            "INSERT INTO travel_plans (request_id, title, summary, itinerary_json, "
            "rationale_json, safety_passed, created_at) "
            "VALUES (?, 't', 's', '[]', '[]', 1, '2026-01-02 03:04:05')",
            (planned,),
        )
        finding_ids = {}
        for request_id in (planned, unplanned):
            finding_ids[request_id] = db.insert_returning_id(
                "INSERT INTO agent_findings (request_id, agent, summary, confidence) "
                "VALUES (?, 'flight_agent', 's', 0.5)",
                (request_id,),
            )
            db.execute(
                "INSERT INTO options (finding_id, name, description) VALUES (?, ?, 'd')",
                (finding_ids[request_id], request_id),
            )

    initialize(database)
    initialize(database)  # and idempotent once migrated

    with connect(database) as db:
        options = {row["name"]: (row["request_id"], row["created_at"]) for row in db.execute(
            "SELECT name, request_id, created_at FROM options"
        ).fetchall()}
        index = db.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
            "AND indexname = 'idx_options_request'"
        ).fetchone()
    assert options == {
        planned: (planned, "2026-01-02 03:04:05"),
        unplanned: (unplanned, None),
    }
    assert index, "the request_id index must exist after migration"


def test_save_plan_stamps_request_id_and_created_at_in_the_sqlite_format(database):
    request_id = str(uuid.uuid4())
    request = TravelRequest(
        origin="Singapore", destination="Japan", departure_date="2026-10-10",
        return_date="2026-10-16", travellers=1, traveller_ages=[30],
        traveller_genders=["prefer_not_to_say"], traveller_accessibility_needs=[[]],
        budget=3000,
    )
    response = PlanResponse(
        request_id=request_id,
        plan=TravelPlan(title="t", summary="s", itinerary=["Day 1"], rationale=["r"]),
        agent_findings=[AgentFinding(
            agent="flight_agent", summary="s", confidence=0.9,
            options=[Option(name="A", description="d")],
        )],
        trace_url="/trace",
    )
    save_plan(database, request, response, [])

    with connect(database) as db:
        option = db.execute("SELECT request_id, created_at FROM options").fetchone()
        finding = db.execute("SELECT created_at FROM agent_findings").fetchone()
    assert option["request_id"] == request_id
    for created in (option["created_at"], finding["created_at"]):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", created), created
