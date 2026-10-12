"""scripts/e2e_test.py sends the demo password to its target, so the target is fenced."""

import pytest

from scripts.e2e_test import base_url_from


@pytest.mark.parametrize("value, expected", [
    ("http://127.0.0.1:5000", "http://127.0.0.1:5000"),
    ("http://localhost:8080/", "http://localhost:8080"),
    ("https://staging.example", "https://staging.example"),
    ("https://user:secret@staging.example/admin?x=1", "https://staging.example"),
    ("http://[::1]:5000", "http://[::1]:5000"),
])
def test_https_or_loopback_targets_are_normalised(value, expected):
    assert base_url_from(value) == expected


@pytest.mark.parametrize("value", [
    "http://staging.example",
    "ftp://127.0.0.1",
    "http://169.254.169.254/latest/meta-data",
    "127.0.0.1:5000",
    "",
])
def test_anything_else_is_refused(value):
    with pytest.raises(SystemExit):
        base_url_from(value)


# --- main(): the fenced target is the one every step uses -------------------

import requests  # noqa: E402

import scripts.e2e_test as e2e  # noqa: E402

SCENARIOS = ("scenario_grounded", "scenario_fallback", "scenario_bias",
             "scenario_injection", "scenario_validation", "scenario_audit_trail")


@pytest.fixture()
def offline(monkeypatch):
    """main() with the network replaced: records the base URL each step got."""
    monkeypatch.setattr(e2e, "DEMO_EMAIL", "e2e@example.com")
    monkeypatch.setattr(e2e, "DEMO_PASSWORD", "not-a-real-password")
    monkeypatch.setattr(e2e.sys, "argv", ["e2e_test.py"])
    monkeypatch.setattr(e2e, "results", [])
    seen = []
    monkeypatch.setattr(e2e, "login", lambda url: seen.append(("login", url)) or object())
    for name in SCENARIOS:
        monkeypatch.setattr(e2e, name, lambda session, url, *rest, _n=name: seen.append((_n, url)))
    return seen


def test_main_sends_every_step_to_the_normalised_target(monkeypatch, offline):
    monkeypatch.setenv("E2E_BASE_URL", "https://user:pw@staging.example/x")
    assert e2e.main() == 0
    assert {url for _step, url in offline} == {"https://staging.example"}
    assert [step for step, _url in offline] == ["login", *SCENARIOS]


def test_main_defaults_to_the_local_server(monkeypatch, offline):
    monkeypatch.delenv("E2E_BASE_URL", raising=False)
    e2e.main()
    assert offline[0] == ("login", "http://127.0.0.1:5000")


def test_main_reports_an_unreachable_server(monkeypatch, offline, capsys):
    def refuse(url):
        raise requests.exceptions.ConnectionError

    monkeypatch.setattr(e2e, "login", refuse)
    monkeypatch.delenv("E2E_BASE_URL", raising=False)
    assert e2e.main() == 2
    assert "Cannot reach http://127.0.0.1:5000" in capsys.readouterr().out


def test_main_refuses_a_plain_http_remote_target(monkeypatch, offline):
    monkeypatch.setenv("E2E_BASE_URL", "http://staging.example")
    with pytest.raises(SystemExit):
        e2e.main()
    assert offline == []
