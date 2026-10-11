"""The login page's `?next=` may only send a user to one of this app's own routes.

The target is rebuilt with url_for() from the route its path matches, so an
open redirect is impossible by construction rather than by a host comparison.
"""

import pytest

from flaskapp import create_app
from tests.test_api import TEST_LOGIN_EMAIL, TEST_LOGIN_PASSWORD, TestConfig


def _login(next_value: str) -> str:
    client = create_app(TestConfig).test_client()
    response = client.post(
        "/", query_string={"next": next_value},
        data={"email": TEST_LOGIN_EMAIL, "password": TEST_LOGIN_PASSWORD},
    )
    assert response.status_code == 302
    return response.headers["Location"]


def test_a_page_of_this_app_is_honoured_with_its_query():
    assert _login("/main?tab=plans") == "/main?tab=plans"


def test_what_login_required_produces_round_trips():
    """login_required sends request.full_path; that exact shape must work."""
    client = create_app(TestConfig).test_client()
    location = client.get("/main").headers["Location"]
    assert "next=" in location
    assert _login("/main") == "/main"


@pytest.mark.parametrize("target", [
    "https://evil.example/main",
    "//evil.example/main",
    "/\\evil.example",
    "http://localhost/main",
    "javascript:alert(1)",
    "main",
    "/no-such-page",
    "",
])
def test_anything_else_falls_back_to_the_main_page(target):
    assert _login(target) == "/main"
