from flaskapp import create_app
from flaskapp.config import Config


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    OPENAI_API_KEY = None


def test_health_page():
    assert create_app(TestConfig).test_client().get("/").status_code == 200


def test_valid_login_redirects_to_dashboard():
    client = create_app(TestConfig).test_client()
    response = client.post("/", data={
        "email": "demo@example.com", "password": "TravelDemo2026!",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200


def test_anonymous_dashboard_redirects_to_login():
    response = create_app(TestConfig).test_client().get("/dashboard")
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
