"""Liveness and readiness probes.

What matters here is less that they return 200 than what they refuse to do:
liveness must not touch the database (a DB outage would otherwise restart every
pod), neither may sit behind the login wall (the kubelet has no session), and
a failing readiness check must not leak the connection error.
"""

from __future__ import annotations

import pytest

import flaskapp.health as health
from flaskapp import create_app
from tests.test_api import TestConfig


@pytest.fixture
def client():
    return create_app(TestConfig).test_client()


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
def test_probe_is_ok_without_a_session(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_liveness_does_not_touch_the_database(client, monkeypatch):
    def unreachable(_target):
        raise AssertionError("liveness opened a database connection")

    monkeypatch.setattr(health, "connect", unreachable)
    assert client.get("/healthz").status_code == 200


def test_readiness_fails_when_the_database_is_unreachable(client, monkeypatch):
    def unreachable(_target):
        raise ConnectionError("could not reach db.internal:5432 as user secret-user")

    monkeypatch.setattr(health, "connect", unreachable)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.get_json() == {"status": "unavailable"}
    assert b"secret-user" not in response.data
