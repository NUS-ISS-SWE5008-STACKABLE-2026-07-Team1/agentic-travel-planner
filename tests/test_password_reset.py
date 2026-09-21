"""Direct password recovery without email delivery or database schema changes."""

from flaskapp import create_app
from tests.test_api import TestConfig


def reset_app(tmp_path):
    class ResetConfig(TestConfig):
        DATABASE = tmp_path / "password-reset.sqlite3"
        SECRET_KEY = "password-reset-test-secret"

    return create_app(ResetConfig)


def test_login_links_to_password_reset(tmp_path):
    app = reset_app(tmp_path)
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b'href="/forgot-password"' in response.data


def test_reset_page_collects_email_and_new_password(tmp_path):
    app = reset_app(tmp_path)
    response = app.test_client().get("/forgot-password")

    assert response.status_code == 200
    assert b'name="email"' in response.data
    assert b'name="password"' in response.data
    assert b'name="confirm_password"' in response.data


def test_registered_user_can_reset_and_sign_in(tmp_path):
    app = reset_app(tmp_path)
    client = app.test_client()

    response = client.post(
        "/forgot-password",
        data={
            "email": "demo@example.com",
            "password": "A-NewJourney2026!",
            "confirm_password": "A-NewJourney2026!",
        },
        follow_redirects=True,
    )
    assert b"its password has been reset" in response.data

    old_login = client.post(
        "/", data={"email": "demo@example.com", "password": "TravelDemo2026!"}
    )
    assert old_login.status_code == 200
    new_login = client.post(
        "/", data={"email": "demo@example.com", "password": "A-NewJourney2026!"}
    )
    assert new_login.status_code == 302


def test_unknown_email_does_not_disclose_account_state(tmp_path):
    app = reset_app(tmp_path)
    response = app.test_client().post(
        "/forgot-password",
        data={
            "email": "missing@example.com",
            "password": "A-NewJourney2026!",
            "confirm_password": "A-NewJourney2026!",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"If an account exists for that email" in response.data


def test_reset_form_enforces_strong_matching_passwords(tmp_path):
    app = reset_app(tmp_path)
    response = app.test_client().post(
        "/forgot-password",
        data={
            "email": "demo@example.com",
            "password": "weak",
            "confirm_password": "different",
        },
    )

    assert response.status_code == 200
    assert b"Use at least 12 characters" in response.data
    assert b"Passwords must match" in response.data
