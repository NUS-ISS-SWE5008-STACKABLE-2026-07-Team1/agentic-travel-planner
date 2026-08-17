"""Cross-user authorization: user B must not reach user A's resources.

Backlog item 13, phase 1. Written before the fix, deliberately, so the suite
demonstrably catches the thing it is meant to catch.

Why this file exists. Authentication and authorization are different questions,
and this codebase answers the first everywhere and the second almost
everywhere. `travel_api_bp.before_request` refuses anyone without a session, so
nothing here is anonymously reachable — but "is signed in" is not "owns this".
Every endpoint below takes a `request_id` from the URL, and the only thing
between user B and user A's data is whether the handler remembered to pass
`session["user_id"]` down to the query. One did not.

Why an automated web scanner does not cover this. ZAP fuzzes the URLs it can
reach; it does not invent another user's UUID, and it has no notion of which
user *should* own a resource. Authorization flaws are the classic blind spot of
dynamic scanning, which is why this is a test and not a scan.

Why the route guards. This file is written to run unchanged on `release` and on
the feature branches ahead of it, which have endpoints `release` does not
(/travel-intents, /user-requests/<id>/session/end, /chat/intake). Rather than
keep two copies in step, each case skips when its route is absent from the URL
map. Coverage widens by itself as branches merge.
"""

from __future__ import annotations

import json

import pytest

from flaskapp import create_app
from flaskapp.database import connect
from tests.test_api import TestConfig

# Fixed ids so a failure names a specific case rather than a random one.
OWNED_REQUEST = "a0000000-0000-4000-8000-000000000001"
ABSENT_REQUEST = "b0000000-0000-4000-8000-000000000002"

INTRUDER_EMAIL = "intruder@example.com"


def _build(tmp_path, name: str):
    """An app whose database and traces live only in this test's tmp_path.

    The owner has one completed planning job with a trace on disk. The intruder
    has an account and nothing else, which is exactly the position an ordinary
    signed-in user of this application is in.
    """

    class _Config(TestConfig):
        DATABASE = tmp_path / f"{name}.sqlite3"
        TRACE_DIR = tmp_path / f"{name}-traces"

    app = create_app(_Config)

    with connect(_Config.DATABASE) as db:
        owner_id = db.execute(
            "SELECT id FROM users WHERE email = ?", (TestConfig.LOGIN_EMAIL,)
        ).fetchone()[0]
        db.execute(
            "INSERT OR IGNORE INTO users (email, password_hash, name) VALUES (?, ?, ?)",
            (INTRUDER_EMAIL, "unused-in-these-tests", "Intruder"),
        )
        intruder_id = db.execute(
            "SELECT id FROM users WHERE email = ?", (INTRUDER_EMAIL,)
        ).fetchone()[0]
        db.execute(
            """INSERT INTO planning_jobs (request_id, user_id, status, request_json)
               VALUES (?, ?, 'completed', '{}')""",
            (OWNED_REQUEST, owner_id),
        )

    # Real traces are sanitized metadata rather than traveller free text, but
    # `has_accessibility_needs` is health-adjacent, which is enough on its own
    # to make cross-user reads worth refusing.
    _Config.TRACE_DIR.mkdir(parents=True, exist_ok=True)
    (_Config.TRACE_DIR / f"{OWNED_REQUEST}.jsonl").write_text(
        json.dumps({
            "timestamp": "2026-08-17T00:00:00+00:00",
            "request_id": OWNED_REQUEST,
            "event": "orchestrator_validation_completed",
            "agent": "orchestrator_agent",
            "details": {"has_accessibility_needs": True, "confidence": 0.91},
            "previous_hash": "GENESIS",
            "hash": "0" * 64,
        }) + "\n",
        encoding="utf-8",
    )
    assert owner_id != intruder_id, "test setup produced one user, not two"
    return app, _Config, owner_id, intruder_id


def _signed_in_as(app, user_id: int, email: str):
    client = app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_id"] = user_id
        session["user_email"] = email
    return client


def _require_route(app, path: str) -> None:
    """Skip when this branch does not have the endpoint under test.

    Matches on the werkzeug rule rather than the concrete URL, so the uuid
    converter in e.g. /api/v1/traces/<uuid:request_id> still resolves.
    """
    adapter = app.url_map.bind("127.0.0.1")
    for method in ("GET", "POST"):
        try:
            adapter.match(path, method=method)
            return
        except Exception:  # NotFound / MethodNotAllowed both mean "try the next"
            continue
    pytest.skip(f"{path} does not exist on this branch")


# --------------------------------------------------------------------------
# Endpoints that already scope by owner. These pass today; they are here so a
# refactor dropping a `session.get("user_id")` argument turns a named test red
# instead of turning nothing red.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("get", f"/api/v1/travel-plans/{OWNED_REQUEST}/status", None),
    ("post", f"/api/v1/travel-plans/{OWNED_REQUEST}/cancel", None),
    ("post", f"/api/v1/user-requests/{OWNED_REQUEST}/session/end", None),
    ("post", f"/api/v1/travel-plans/{OWNED_REQUEST}/feedback",
     {"rating": "down", "comment": "this plan needs a great deal more detail than it has"}),
])
def test_another_users_request_is_not_reachable(tmp_path, method, path, body):
    app, _config, _owner_id, intruder_id = _build(tmp_path, "scoped")
    _require_route(app, path)
    client = _signed_in_as(app, intruder_id, INTRUDER_EMAIL)

    response = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)

    # 404 rather than 403 throughout: refusing without confirming the resource
    # exists is the better answer, and it is what the siblings already do.
    assert response.status_code == 404, (
        f"{method.upper()} {path} returned {response.status_code} to a user who does "
        f"not own the request; expected 404"
    )


def test_the_owner_can_still_reach_their_own_trace(tmp_path):
    """The mirror of the above. A rule that refuses everybody is not a fix."""
    app, _config, owner_id, _intruder_id = _build(tmp_path, "owner-ok")
    client = _signed_in_as(app, owner_id, TestConfig.LOGIN_EMAIL)

    trace = client.get(f"/api/v1/traces/{OWNED_REQUEST}")
    assert trace.status_code == 200
    assert trace.get_json()["request_id"] == OWNED_REQUEST
    assert b"has_accessibility_needs" in trace.data


# --------------------------------------------------------------------------
# The gap this file was written to catch.
# --------------------------------------------------------------------------

def test_traces_are_not_readable_by_another_signed_in_user(tmp_path):
    """`GET /api/v1/traces/<uuid>` had authentication but no authorization.

    The handler read TRACE_DIR/<request_id>.jsonl with no owner check, while
    every sibling endpoint passed `session["user_id"]` into its query. Any
    signed-in user could read any trace whose id they had — from a shared
    /chat/<uuid> link, a referrer header, or the admin activity view.
    """
    app, _config, _owner_id, intruder_id = _build(tmp_path, "traces")
    client = _signed_in_as(app, intruder_id, INTRUDER_EMAIL)

    response = client.get(f"/api/v1/traces/{OWNED_REQUEST}")

    assert response.status_code == 404, (
        "a signed-in user read another user's trace: "
        f"{response.status_code} {response.get_data(as_text=True)[:200]}"
    )
    assert b"has_accessibility_needs" not in response.data


def test_a_trace_with_no_owning_request_is_not_served(tmp_path):
    """A trace file whose planning_jobs row is missing must not fall open.

    Guards the shape of the fix as much as its presence: an ownership check
    written as "no row means no restriction" would pass the test above and
    still serve orphaned traces to anyone.
    """
    app, config, _owner_id, intruder_id = _build(tmp_path, "orphan")
    (config.TRACE_DIR / f"{ABSENT_REQUEST}.jsonl").write_text(
        json.dumps({"event": "orphaned", "details": {}}) + "\n", encoding="utf-8"
    )
    client = _signed_in_as(app, intruder_id, INTRUDER_EMAIL)

    assert client.get(f"/api/v1/traces/{ABSENT_REQUEST}").status_code == 404


# --------------------------------------------------------------------------
# Anonymous access. The blueprint guard covers these; nothing asserted it.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    f"/api/v1/traces/{OWNED_REQUEST}",
    f"/api/v1/travel-plans/{OWNED_REQUEST}/status",
    "/api/v1/admin/activity",
])
def test_the_api_refuses_anonymous_callers(tmp_path, path):
    app, _config, _owner_id, _intruder_id = _build(tmp_path, "anon-api")
    _require_route(app, path)
    assert app.test_client().get(path).status_code == 401


@pytest.mark.parametrize("path", ["/main", "/admin", f"/chat/{OWNED_REQUEST}"])
def test_browser_pages_redirect_anonymous_callers_to_sign_in(tmp_path, path):
    app, _config, _owner_id, _intruder_id = _build(tmp_path, "anon-pages")
    _require_route(app, path)
    response = app.test_client().get(path)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/")
