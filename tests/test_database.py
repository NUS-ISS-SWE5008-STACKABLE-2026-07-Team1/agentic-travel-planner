import sqlite3

from werkzeug.security import generate_password_hash

from flaskapp import create_app
from flaskapp.config import Config
from flaskapp.database import SCHEMA
from flaskapp.database import initialize, save_plan
from flaskapp.travel_ai.schemas import PlanResponse, TravelPlan, TravelRequest


def test_schema_creates_all_application_tables():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA)
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "users", "travel_requests", "travel_plans", "agent_findings", "options",
        "a2a_messages", "audit_events",
        "planning_jobs", "agent_runs",
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
