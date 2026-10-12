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
