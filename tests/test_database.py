import sqlite3

from werkzeug.security import generate_password_hash

from flaskapp import create_app
from flaskapp.config import Config
from flaskapp.database import SCHEMA_SQLITE
from flaskapp.database import initialize, save_plan
from flaskapp.travel_ai.schemas import PlanResponse, TravelPlan, TravelRequest


def test_schema_creates_all_application_tables():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_SQLITE)
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "users", "travel_requests", "travel_plans", "agent_findings", "options",
        "a2a_messages", "audit_events",
        "planning_jobs", "agent_runs", "plan_feedback",
    } <= tables


def test_app_seeds_database_user_and_authenticates(tmp_path):
    class DatabaseConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        DATABASE = tmp_path / "test.sqlite3"
        LOGIN_EMAIL = "local@example.com"
        LOGIN_PASSWORD_HASH = generate_password_hash("local-password")

    client = create_app(DatabaseConfig).test_client()
    response = client.post("/", data={
        "email": "local@example.com", "password": "local-password",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/main")


def test_completed_plan_is_persisted_without_column_mismatch(tmp_path):
    database = tmp_path / "plan.sqlite3"
    initialize(database)
    request = TravelRequest(
        origin="Singapore", destination="Japan", departure_date="2026-10-10",
        return_date="2026-10-16", travellers=1, traveller_ages=[30],
        traveller_genders=["prefer_not_to_say"], traveller_accessibility_needs=[[]],
        budget=3000,
    )
    response = PlanResponse(
        request_id="11111111-1111-4111-8111-111111111111",
        plan=TravelPlan(
            title="Japan plan", summary="A test plan", itinerary=["Day 1"],
            rationale=["Matches budget"],
        ),
        agent_findings=[], trace_url="/trace",
    )
    save_plan(database, request, response, [])
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT title FROM travel_plans").fetchone()[0] == "Japan plan"


def _plan_response(request_id: str) -> PlanResponse:
    return PlanResponse(
        request_id=request_id,
        plan=TravelPlan(
            title="Japan plan", summary="A test plan", itinerary=["Day 1"],
            rationale=["Matches budget"],
        ),
        agent_findings=[], trace_url="/trace",
    )


def _request(**overrides) -> TravelRequest:
    return TravelRequest(**{
        "origin": "Singapore", "destination": "Japan", "departure_date": "2026-10-10",
        "return_date": "2026-10-16", "travellers": 1, "traveller_ages": [30],
        "traveller_genders": ["prefer_not_to_say"], "traveller_accessibility_needs": [[]],
        "budget": 3000, **overrides,
    })


def test_cities_are_persisted_alongside_countries(tmp_path):
    """The columns the Hotel & Transport agent joins its inventory against."""
    database = tmp_path / "plan.sqlite3"
    initialize(database)
    save_plan(
        database,
        _request(origin_city="Singapore", destination_city="Tokyo"),
        _plan_response("11111111-1111-4111-8111-111111111111"),
        [],
    )
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT origin, destination, origin_city, destination_city FROM travel_requests"
        ).fetchone()
    assert row == ("Singapore", "Japan", "Singapore", "Tokyo")


def test_a_country_only_request_stores_null_cities(tmp_path):
    """Country-only requests predate city intake and must still persist."""
    database = tmp_path / "plan.sqlite3"
    initialize(database)
    save_plan(database, _request(), _plan_response("22222222-2222-4222-8222-222222222222"), [])
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT origin_city, destination_city FROM travel_requests"
        ).fetchone()
    assert row == (None, None)


def test_initialize_is_idempotent_and_adds_city_columns_to_an_old_database(tmp_path):
    """The upgrade path for the committed database: add columns, keep rows."""
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA_SQLITE)
        # Simulate a pre-city database by dropping the columns back out.
        connection.execute("ALTER TABLE travel_requests DROP COLUMN origin_city")
        connection.execute("ALTER TABLE travel_requests DROP COLUMN destination_city")
        connection.execute(
            """INSERT INTO travel_requests
               (id, origin, destination, departure_date, return_date, travellers,
                budget, currency, risk_tolerance)
               VALUES ('old', 'Singapore', 'Japan', '2026-01-01', '2026-01-05', 1,
                       1000, 'SGD', 'medium')"""
        )

    initialize(database)
    initialize(database)  # second run must be a no-op, not an error

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(travel_requests)")}
        preserved = connection.execute(
            "SELECT origin, origin_city FROM travel_requests WHERE id = 'old'"
        ).fetchone()
    assert {"origin_city", "destination_city"} <= columns
    assert preserved == ("Singapore", None)


def test_ensure_planning_job_does_not_reset_a_running_job(tmp_path):
    """The whole reason this is separate from `create_planning_job`.

    `create_planning_job` upserts back to 'queued' so a resubmit works. Doing
    that here would take a job that is mid-flight and report it to `/admin` as
    queued, so the A2A path needs a create-if-absent that leaves an existing
    row alone.
    """
    from flaskapp.database import connect, create_planning_job, ensure_planning_job
    from flaskapp.database import update_planning_job

    path = tmp_path / "planner.sqlite3"
    initialize(path)

    created = ensure_planning_job(path, "req-1", None, {"origin": "Singapore"})
    assert created is True
    update_planning_job(path, "req-1", "processing")

    created_again = ensure_planning_job(path, "req-1", None, {"origin": "Tokyo"})

    assert created_again is False, "second call must not insert a second row"
    with connect(path) as db:
        rows = db.execute(
            "SELECT request_id, status FROM planning_jobs WHERE request_id = ?",
            ("req-1",),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "processing", "a live job was reset to queued"

    # Contrast: create_planning_job deliberately does reset, and that behaviour
    # must stay intact for `jobs.submit_plan`'s resubmit path.
    create_planning_job(path, "req-1", None, {"origin": "Tokyo"})
    with connect(path) as db:
        status = db.execute(
            "SELECT status FROM planning_jobs WHERE request_id = ?", ("req-1",)
        ).fetchone()["status"]
    assert status == "queued"


def test_ensure_planning_job_satisfies_the_agent_runs_foreign_key(tmp_path):
    """`agent_runs.request_id` references `planning_jobs`, with FKs enforced."""
    from flaskapp.database import ensure_planning_job, save_agent_run

    path = tmp_path / "planner.sqlite3"
    initialize(path)

    ensure_planning_job(path, "a2a-task-1", None, {"origin": "Singapore"})
    save_agent_run(path, "a2a-task-1", "flight_agent", "completed")

    from flaskapp.database import connect
    with connect(path) as db:
        row = db.execute(
            "SELECT agent, status FROM agent_runs WHERE request_id = ?",
            ("a2a-task-1",),
        ).fetchone()
    assert row["agent"] == "flight_agent"
