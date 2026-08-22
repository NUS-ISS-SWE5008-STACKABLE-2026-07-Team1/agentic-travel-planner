import tempfile
from pathlib import Path

from flaskapp import create_app
from flaskapp.config import Config
from flaskapp.database import connect, get_admin_activity, get_platform_dashboard

# A dedicated SQLite file outside any developer's instance/ directory. Config.DATABASE
# now honours DATABASE_URL when it is set in the environment (e.g. from a real
# .env.secrets on a developer machine); without this override, TestConfig would
# inherit that DSN and every test using it would write schema DDL and a demo-user
# seed to the live Supabase database instead of a throwaway SQLite file.
_TEST_DATABASE = Path(tempfile.mkdtemp(prefix="travel_planner_test_api_")) / "test.sqlite3"


class TestConfig(Config):
    """Credential-free by construction.

    Config reads the process environment at import, so a developer with a working
    .env would otherwise see 'no credential configured' tests pass or fail
    depending on their own machine. Every provider key is cleared here so the
    unconfigured path is what the suite actually exercises.
    """

    TESTING = True
    WTF_CSRF_ENABLED = False
    DATABASE = _TEST_DATABASE
    LOGIN_EMAIL = "demo@example.com"
    LOGIN_PASSWORD_HASH = (
        "scrypt:32768:8:1$99T3BfVwYO8CnqNC$"
        "c85a15f2f167616564085724c37c79fc2ad151e306e5ec0414759a0f8a6eba28"
        "a179494a8d39bfd838986ebbb2daa4d0586da0bee718d299fce4a89a7de45a95"
    )
    LLM_PROVIDER = "auto"
    AZURE_OPENAI_API_KEY = None
    AZURE_OPENAI_ENDPOINT = None
    OPENAI_API_KEY = None
    ANTHROPIC_API_KEY = None
    GOOGLE_API_KEY = None
    DEEPSEEK_API_KEY = None
    XAI_API_KEY = None
    META_API_KEY = None
    LLM_API_KEY = None
    # The L2 classifier is off for the suite at large. UR-083 requires the tests
    # to run with no access to a live provider, and a fail-closed guardrail left
    # on would otherwise turn every unrelated API test into a 422 the moment it
    # could not reach a model. The classifier has its own coverage in
    # tests/adversarial/, where the model is stubbed explicitly.
    GUARDRAIL_LLM_ENABLED = False


class ConfiguredConfig(TestConfig):
    """A credential is present, so requests reach validation instead of stopping at 503."""

    LLM_PROVIDER = "openai"
    LLM_MODEL = "gpt-4.1-mini"
    OPENAI_API_KEY = "test-key-not-used"


def test_health_page():
    assert create_app(TestConfig).test_client().get("/").status_code == 200


def test_valid_login_redirects_to_main():
    client = create_app(TestConfig).test_client()
    response = client.post("/", data={
        "email": "demo@example.com", "password": "TravelDemo2026!",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/main")
    main = client.get("/main")
    assert main.status_code == 200
    assert b"Traveller" in main.data
    assert b"Select departure country" in main.data
    assert b"Select destination country" in main.data
    assert b'id="origin"' in main.data
    assert b'id="destination"' in main.data


def test_manual_form_defaults_origin_to_signed_in_users_country():
    client = create_app(TestConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_country"] = "Singapore"
    response = client.get("/main")
    assert response.status_code == 200
    assert b'<option value="Singapore" selected>Singapore</option>' in response.data
    assert b'id="origin" name="origin"' in response.data
    assert b'name="traveller_preference"' not in response.data  # Rendered dynamically by JS.


def test_the_intake_form_offers_dependent_city_selects():
    """Cities are embedded in the page, so the select needs no extra request."""
    import json
    import re

    client = create_app(TestConfig).test_client()
    client.post("/", data={"email": "demo@example.com", "password": "TravelDemo2026!"})
    html = client.get("/main").get_data(as_text=True)

    assert 'id="origin_city"' in html and 'id="destination_city"' in html

    embedded = re.search(
        r'<script type="application/json" id="city-options">(.*?)</script>', html, re.S
    )
    assert embedded, "city options must be embedded for the dependent select to work"
    options = json.loads(embedded.group(1))
    # A multi-airport city must advertise every airport in its label, which is
    # what tells a traveller that picking the city covers all of them.
    london = next(c for c in options["United Kingdom"] if c["slug"] == "gb-london")
    assert london["label"] == "London (LHR/LGW/STN/LTN)"


def test_anonymous_main_redirects_to_login():
    response = create_app(TestConfig).test_client().get("/main")
    assert response.status_code == 302


def test_plan_endpoint_requires_key():
    client = create_app(TestConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    response = client.post("/api/v1/travel-plans", json={})
    assert response.status_code == 503


def test_plan_endpoint_rejects_anonymous_user():
    client = create_app(TestConfig).test_client()
    assert client.post("/api/v1/travel-plans", json={}).status_code == 401


def test_plan_submission_creates_async_chat_job(tmp_path, monkeypatch):
    class AzureConfig(TestConfig):
        DATABASE = tmp_path / "async.sqlite3"
        AZURE_OPENAI_API_KEY = "test-key"
        AZURE_OPENAI_ENDPOINT = "https://example.openai.azure.com/"
        AZURE_OPENAI_DEPLOYMENT = "test-deployment"

    class FakeJob:
        request_id = "11111111-1111-4111-8111-111111111111"
        status = "queued"

    monkeypatch.setattr("flaskapp.travel_ai.api.submit_plan", lambda *_args: FakeJob())
    client = create_app(AzureConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_id"] = 7
    response = client.post("/api/v1/travel-plans", json={
        "origin": "Singapore", "destination": "Japan",
        "departure_date": "2026-10-10", "return_date": "2026-10-16",
        "travellers": 1, "traveller_ages": [30],
        "traveller_genders": ["prefer_not_to_say"],
        "traveller_accessibility_needs": [[]], "budget": 3000,
    })
    assert response.status_code == 202
    assert response.get_json()["chat_url"].endswith(FakeJob.request_id)


def test_the_api_accepts_cities_and_still_accepts_country_only(tmp_path, monkeypatch):
    """City fields are additive: existing country-only clients must not break."""
    class AzureConfig(TestConfig):
        DATABASE = tmp_path / "cities.sqlite3"
        AZURE_OPENAI_API_KEY = "test-key"
        AZURE_OPENAI_ENDPOINT = "https://example.openai.azure.com/"
        AZURE_OPENAI_DEPLOYMENT = "test-deployment"

    class FakeJob:
        request_id = "11111111-1111-4111-8111-111111111111"
        status = "queued"

    monkeypatch.setattr("flaskapp.travel_ai.api.submit_plan", lambda *_args: FakeJob())
    client = create_app(AzureConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_id"] = 7

    base = {
        "origin": "Singapore", "destination": "Japan",
        "departure_date": "2026-10-10", "return_date": "2026-10-16",
        "travellers": 1, "traveller_ages": [30],
        "traveller_genders": ["prefer_not_to_say"],
        "traveller_accessibility_needs": [[]], "budget": 3000,
    }
    with_cities = {**base, "origin_city": "Singapore", "destination_city": "Tokyo"}

    assert client.post("/api/v1/travel-plans", json=with_cities).status_code == 202
    assert client.post("/api/v1/travel-plans", json=base).status_code == 202


def test_chat_page_requires_login_and_renders_for_user():
    request_id = "11111111-1111-4111-8111-111111111111"
    client = create_app(TestConfig).test_client()
    assert client.get(f"/chat/{request_id}").status_code == 302
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_name"] = "Alicia"
    response = client.get(f"/chat/{request_id}")
    assert response.status_code == 200
    assert b"Your AI team is working" in response.data
    assert b'id="planning-progress-wrap"' in response.data
    assert b'id="intake-conversation-history"' in response.data
    assert b'id="plan-content"' in response.data
    assert b'id="agent-activity"' not in response.data
    assert b"cancel-and-home" in response.data
    assert b"Stop planning and return home?" in response.data
    assert b"Keep planning" in response.data


def test_intake_chat_requires_login_and_hosts_orchestrator_clarification():
    client = create_app(TestConfig).test_client()
    assert client.get("/chat/intake").status_code == 302
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_name"] = "Alicia"
    response = client.get("/chat/intake")
    assert response.status_code == 200
    assert b'data-auto-start="true"' in response.data
    assert b'<section class="d-none" aria-labelledby="activity-heading">' in response.data
    assert b"Travel assistant" in response.data
    assert b"/api/v1/travel-intents/resolve" in response.data


def test_main_page_provides_country_options_required_by_intake_script():
    client = create_app(TestConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_name"] = "Alicia"

    response = client.get("/main")

    assert response.status_code == 200
    assert b'id="country-options"' in response.data


def test_admin_button_is_available_on_both_chat_modes():
    client = create_app(TestConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_email"] = TestConfig.ADMIN_EMAIL
    intake = client.get("/chat/intake")
    planning = client.get("/chat/11111111-1111-4111-8111-111111111111")
    assert b'href="/admin"' in intake.data
    assert b'href="/admin"' in planning.data


def test_cancel_endpoint_cancels_authenticated_users_job(monkeypatch):
    request_id = "11111111-1111-4111-8111-111111111111"
    cancelled = type("Job", (), {"request_id": request_id, "status": "cancelled"})()
    monkeypatch.setattr(
        "flaskapp.travel_ai.api.cancel_job",
        lambda supplied_id, user_id: cancelled if supplied_id == request_id and user_id == 7 else None,
    )
    client = create_app(TestConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_id"] = 7
    response = client.post(f"/api/v1/travel-plans/{request_id}/cancel")
    assert response.status_code == 200
    assert response.get_json()["status"] == "cancelled"


def test_completed_request_session_stays_active_until_user_leaves_chat(tmp_path):
    class SessionConfig(TestConfig):
        DATABASE = tmp_path / "request-session.sqlite3"

    client = create_app(SessionConfig).test_client()
    with connect(SessionConfig.DATABASE) as db:
        user_id = db.execute("SELECT id FROM users WHERE email = ?", (TestConfig.LOGIN_EMAIL,)).fetchone()[0]
        request_id = "11111111-1111-4111-8111-111111111112"
        db.execute(
            """INSERT INTO planning_jobs (request_id, user_id, status, request_json)
               VALUES (?, ?, 'completed', '{}')""",
            (request_id, user_id),
        )
        assert db.execute(
            "SELECT session_status FROM planning_jobs WHERE request_id = ?", (request_id,)
        ).fetchone()[0] == "active"
    with client.session_transaction() as user_session:
        user_session["authenticated"] = True
        user_session["user_id"] = user_id
    response = client.post(f"/api/v1/user-requests/{request_id}/session/end")
    assert response.status_code == 200
    with connect(SessionConfig.DATABASE) as db:
        row = db.execute(
            "SELECT session_status, session_ended_at FROM planning_jobs WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        assert row["session_status"] == "ended"
        assert row["session_ended_at"] is not None
    assert get_admin_activity(SessionConfig.DATABASE)[0]["display_status"] == "Completed"


def test_completed_plan_feedback_requires_comment_for_thumbs_down(tmp_path):
    class FeedbackConfig(TestConfig):
        DATABASE = tmp_path / "feedback.sqlite3"

    client = create_app(FeedbackConfig).test_client()
    client.post("/", data={"email": "demo@example.com", "password": "TravelDemo2026!"})
    request_id = "11111111-1111-4111-8111-111111111111"
    with connect(FeedbackConfig.DATABASE) as db:
        user_id = db.execute("SELECT id FROM users WHERE email = 'demo@example.com'").fetchone()[0]
        db.execute(
            "INSERT INTO planning_jobs (request_id, user_id, status, request_json) VALUES (?, ?, 'completed', '{}')",
            (request_id, user_id),
        )
    short = client.post(f"/api/v1/travel-plans/{request_id}/feedback", json={
        "rating": "down", "comment": "This plan needs more detail",
    })
    assert short.status_code == 422
    accepted = client.post(f"/api/v1/travel-plans/{request_id}/feedback", json={
        "rating": "down",
        "comment": "This plan needs more detail about transport timing cost and accessibility options",
    })
    assert accepted.status_code == 201
    dashboard = get_platform_dashboard(FeedbackConfig.DATABASE)
    assert dashboard["needs_improvement_rate"] == 100.0
    assert dashboard["average_feedback"] == 0.0


def test_admin_page_and_activity_require_configured_admin(tmp_path):
    class AdminConfig(TestConfig):
        DATABASE = tmp_path / "admin.sqlite3"
        ADMIN_EMAIL = "admin@example.com"
        ADMIN_EMAILS = ("admin@example.com", "second-admin@example.com")

    client = create_app(AdminConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_email"] = "user@example.com"
    assert client.get("/admin").status_code == 403
    assert client.get("/api/v1/admin/activity").status_code == 403

    with client.session_transaction() as session:
        session["user_email"] = "admin@example.com"
        session["user_name"] = "Admin"
    assert client.get("/admin").status_code == 200
    admin_page = client.get("/admin")
    assert b'id="processing-timeline-dialog"' in admin_page.data
    assert b'id="close-processing-timeline"' in admin_page.data
    assert b'id="live-processing-log"' in admin_page.data
    assert b'id="live-request-conversation"' in admin_page.data
    assert b"User request details" not in admin_page.data
    assert b"Orchestrator conversation" not in admin_page.data
    assert b"Agent stages" not in admin_page.data
    assert b"Processing Timeline" in admin_page.data
    assert b"User Requests" in admin_page.data
    assert b"<th>Session</th>" in admin_page.data
    response = client.get("/api/v1/admin/activity")
    assert response.status_code == 200
    dashboard = response.get_json()
    assert dashboard["requests"] == []
    assert dashboard["consumption"]["totals"] == {
        "request_count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
    }
    assert dashboard["platform"]["total_requests"] == 0
    assert dashboard["logs"] == []
    # Five agents, the deterministic gates, and the L2 classifier.
    assert len(dashboard["prompts"]) == 7
    assert "LLM guardrail classifier (L2)" in {item["agent"] for item in dashboard["prompts"]}

    with client.session_transaction() as session:
        session["user_email"] = "SECOND-ADMIN@example.com"
    assert client.get("/admin").status_code == 200
    main = client.get("/main")
    assert b'target="_blank"' in main.data
    assert b'rel="noopener noreferrer"' in main.data


def test_admin_can_register_database_managed_administrator(tmp_path):
    class AdminConfig(TestConfig):
        DATABASE = tmp_path / "registered-admin.sqlite3"
        ADMIN_EMAIL = "owner@example.com"
        ADMIN_EMAILS = ()

    client = create_app(AdminConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_email"] = "owner@example.com"
    response = client.post("/api/v1/admin/administrators", json={
        "name": "Operations Admin", "email": "ops@example.com",
        "password": "StrongAdmin2026!",
    })
    assert response.status_code == 201
    with client.session_transaction() as session:
        session["user_email"] = "ops@example.com"
    assert client.get("/admin").status_code == 200


def test_registration_creates_account_and_signs_user_in(tmp_path):
    class RegistrationConfig(TestConfig):
        DATABASE = tmp_path / "registration.sqlite3"

    client = create_app(RegistrationConfig).test_client()
    response = client.post("/register", data={
        "name": "Alicia Tan",
        "email": "alicia@example.com",
        "country": "Singapore",
        "birthday": "1995-04-12",
        "password": "StrongJourney9!",
        "confirm_password": "StrongJourney9!",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/main")
    assert b"Alicia Tan" in client.get("/main").data
    assert b'<option value="Singapore" selected>Singapore</option>' in client.get("/main").data
    with connect(RegistrationConfig.DATABASE) as db:
        user = db.execute(
            "SELECT name, email, country, birthday, password_hash FROM users WHERE email = ?",
            ("alicia@example.com",),
        ).fetchone()
    assert tuple(user[:4]) == ("Alicia Tan", "alicia@example.com", "Singapore", "1995-04-12")
    assert "StrongJourney9!" not in user["password_hash"]


def test_registration_rejects_weak_and_mismatched_password(tmp_path):
    class RegistrationConfig(TestConfig):
        DATABASE = tmp_path / "invalid-registration.sqlite3"

    response = create_app(RegistrationConfig).test_client().post("/register", data={
        "name": "Alicia Tan", "email": "alicia@example.com", "country": "Singapore",
        "birthday": "1995-04-12", "password": "weakpassword",
        "confirm_password": "different",
    })
    assert response.status_code == 200
    assert b"Use at least 12 characters" in response.data
    assert b"Passwords must match" in response.data


def test_registration_rejects_country_outside_selection_list(tmp_path):
    class RegistrationConfig(TestConfig):
        DATABASE = tmp_path / "invalid-country.sqlite3"

    response = create_app(RegistrationConfig).test_client().post("/register", data={
        "name": "Alicia Tan", "email": "alicia@example.com", "country": "Atlantis",
        "birthday": "1995-04-12", "password": "StrongJourney9!",
        "confirm_password": "StrongJourney9!",
    })
    assert response.status_code == 200
    assert b"Not a valid choice" in response.data


VALID_PLAN = {
    "origin": "Singapore", "destination": "Tokyo",
    "departure_date": "2026-10-05", "return_date": "2026-10-19",
    "travellers": 1, "traveller_ages": [34], "traveller_genders": ["male"],
    "traveller_accessibility_needs": [[]], "budget": 8000.0, "currency": "SGD",
}


def test_cross_field_validation_error_returns_json_not_an_html_error_page(monkeypatch):
    """A model_validator failure must serialize.

    Pydantic puts the raw ValueError object into ctx["error"] for validator
    failures, so jsonify(exc.errors()) raised TypeError and Flask served its HTML
    error page. The browser then failed on "Unexpected token '<'" instead of
    showing the traveller what was wrong with their dates.
    """
    monkeypatch.setattr("flaskapp.travel_ai.api.submit_plan", lambda *_args: None)
    client = create_app(ConfiguredConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True

    response = client.post("/api/v1/travel-plans", json={
        **VALID_PLAN, "departure_date": "2026-10-19", "return_date": "2026-10-05",
    })

    assert response.status_code == 422
    assert response.is_json
    body = response.get_json()
    assert "return_date must be on or after departure_date" in str(body["details"])
