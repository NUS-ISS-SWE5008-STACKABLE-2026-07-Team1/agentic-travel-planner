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


def test_api_csrf_failure_is_json_with_an_actionable_message(tmp_path):
    class CsrfConfig(TestConfig):
        DATABASE = tmp_path / "csrf.sqlite3"
        WTF_CSRF_ENABLED = True

    client = create_app(CsrfConfig).test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_id"] = 1

    response = client.post("/api/v1/travel-plans", json={})

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json()["code"] == "csrf_token_invalid"
    assert "Refresh the page" in response.get_json()["error"]


def test_planner_handles_non_json_server_errors_without_parsing_html():
    javascript = __import__("pathlib").Path("flaskapp/static/js/app.js").read_text(
        encoding="utf-8"
    )
    assert 'contentType.includes("application/json")' in javascript
    assert "The server could not process the request" in javascript


def _policy() -> dict[str, set[str]]:
    """The live policy, parsed into {directive: {sources}}.

    Read from the constant rather than from a response, so a test can say what
    the policy *is* without asserting the exact string — a full-string equality
    test would just restate the constant and fail on every harmless reordering.
    """
    from flaskapp import CONTENT_SECURITY_POLICY

    parsed = {}
    for directive in CONTENT_SECURITY_POLICY.split(";"):
        parts = directive.split()
        if parts:
            parsed[parts[0]] = set(parts[1:])
    return parsed


@pytest.mark.parametrize("path", ["/", "/register", "/forgot-password"])
def test_csp_is_set_on_every_page(client, path):
    """The header ZAP reported missing (10038) on every page it reached."""
    response = client.get(path)
    assert response.headers.get("Content-Security-Policy"), f"{path} has no CSP"


def test_csp_reaches_authenticated_pages_too(client):
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user_id"] = 1
    for path in ("/main", "/chat"):
        response = client.get(path)
        assert response.headers.get("Content-Security-Policy"), f"{path} has no CSP"


def test_csp_forbids_inline_and_wildcards():
    """The assertion with real content, and the realistic regression.

    A CSP that breaks a page fails loudly in a browser, and the tempting fix is
    to paste 'unsafe-inline' back in — which silently returns the policy to
    roughly no protection against injected script, while still looking like a
    policy. This fails when someone does that.
    """
    offenders = {
        name: sources
        for name, sources in _policy().items()
        if {"'unsafe-inline'", "'unsafe-eval'", "*"} & sources
    }
    assert not offenders, f"unsafe sources in the policy: {offenders}"
    assert _policy()["default-src"] == {"'self'"}


def test_csp_defines_the_directives_with_no_default_src_fallback():
    """frame-ancestors, form-action and base-uri do NOT inherit default-src.

    Pinned because they read like noise next to `default-src 'self'` and are
    exactly the kind of line a later reader deletes as redundant. They are not:
    omitting any of them leaves a real gap, and ZAP raises a Medium for each.
    """
    policy = _policy()
    for directive in ("frame-ancestors", "form-action", "base-uri"):
        assert directive in policy, f"{directive} is missing and does not fall back"


def test_no_template_carries_an_inline_style_attribute():
    """A `style=` attribute is inline style, which `style-src` forbids.

    The browser does not complain in any way a user would notice: it drops the
    declaration and renders the element unstyled. The one instance this app had
    was the planning progress bar's starting width, now in `app.css`.

    Scanning every template rather than pinning that one element, because the
    risk is not the fixed case — it is the next `style=` somebody adds, which
    will silently do nothing. A violation here is cheap to fix (move it to the
    stylesheet); finding out from a screenshot is not.
    """
    import pathlib

    offenders = []
    for template in sorted(pathlib.Path("flaskapp/templates").glob("*.html")):
        for tag in re.findall(r"<[a-zA-Z][^>]*\sstyle=[\"'][^\"']*[\"'][^>]*>",
                              template.read_text(encoding="utf-8")):
            offenders.append(f"{template.name}: {tag[:100]}")

    assert not offenders, (
        "inline style attributes are blocked by the CSP; move these into "
        "static/css/app.css:\n  " + "\n  ".join(offenders)
    )


def test_the_progress_bar_has_a_starting_width_in_the_stylesheet():
    """The other half of the test above.

    Removing the `style=` attribute without adding the rule would leave the bar
    at zero width — a change that passes every other test in this file.
    """
    import pathlib

    css = pathlib.Path("flaskapp/static/css/app.css").read_text(encoding="utf-8")
    rule = re.search(r"\.planning-progress-bar\s*\{([^}]*)\}", css)
    assert rule, ".planning-progress-bar rule not found in app.css"
    assert "width:" in rule.group(1), (
        "the progress bar has no width in CSS; its inline style= was removed "
        "for the CSP, so this rule is what gives it a starting width"
    )


def test_every_external_origin_in_templates_and_css_is_allowed_by_the_policy():
    """Scan the source for external origins; each must appear in the policy.

    Same reasoning as the integrity test below: checking known hosts would pass
    forever while the NEXT origin someone adds goes unlisted and is silently
    blocked.

    The stylesheet is read as well as the templates, and that is the whole
    point of this test. `app.css` opens with an `@import` of a Google Fonts
    stylesheet, so fonts.googleapis.com never appears in any template — a
    template-only scan would have declared the policy complete while the two
    brand typefaces failed to load on every page, with no error anyone sees.
    """
    import pathlib

    sources = list(pathlib.Path("flaskapp/templates").glob("*.html"))
    sources += list(pathlib.Path("flaskapp/static/css").glob("*.css"))
    origins = set()
    for path in sources:
        origins.update(
            re.findall(r"https://[A-Za-z0-9.-]+", path.read_text(encoding="utf-8"))
        )

    allowed = {source for sources_ in _policy().values() for source in sources_}
    missing = sorted(origin for origin in origins if origin not in allowed)
    assert not missing, (
        "origins used by the app but not allowed by the CSP: " + ", ".join(missing)
    )


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
