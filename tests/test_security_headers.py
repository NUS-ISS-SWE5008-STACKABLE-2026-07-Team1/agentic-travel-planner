"""Response headers and subresource integrity.

Both fixes here came from the first authenticated DAST run (backlog item 13).
They are one-liners, which is exactly why they need tests: a one-line change is
the kind that gets lost in a merge without anyone noticing, and neither failure
is visible from using the app.
"""

from __future__ import annotations

import re

import pytest

from flaskapp import create_app
from tests.test_api import TestConfig

CDN = "cdn.jsdelivr.net"


@pytest.fixture
def client():
    return create_app(TestConfig).test_client()


@pytest.mark.parametrize("header,value", [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
])
@pytest.mark.parametrize("path", ["/", "/register"])
def test_security_headers_are_set(client, path, header, value):
    """ZAP reported both missing on every page it reached."""
    response = client.get(path)
    assert response.headers.get(header) == value, (
        f"{path} is missing {header}: {value}"
    )


def test_security_headers_reach_authenticated_pages_too(client):
    """after_request applies app-wide; this pins that it covers logged-in views."""
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_id"] = 1
    response = client.get("/main")
    assert response.status_code == 200
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_every_cdn_resource_declares_integrity():
    """Anything loaded from a third-party CDN must carry a checksum.

    Without `integrity`, a compromised CDN serves whatever it likes and the
    browser runs it. Semgrep and ZAP independently flagged two files on
    /admin — bootstrap-icons and chart.js — that had no checksum while every
    other CDN reference in the project did.

    This scans the templates as text rather than checking two known lines, so a
    NEW unprotected CDN reference fails too. That is the actual risk: the two
    known ones are fixed, and the next one added is the one nobody looks at.
    """
    import pathlib

    offenders = []
    for template in sorted(pathlib.Path("flaskapp/templates").glob("*.html")):
        text = template.read_text(encoding="utf-8")
        for tag in re.findall(r"<(?:script|link)\b[^>]*>", text):
            if CDN not in tag:
                continue
            # preconnect/dns-prefetch open a connection, they do not load code.
            if re.search(r'rel="(?:preconnect|dns-prefetch)"', tag):
                continue
            if "integrity=" not in tag:
                offenders.append(f"{template.name}: {tag[:110]}")

    assert not offenders, "CDN resources without an integrity checksum:\n  " + "\n  ".join(offenders)
