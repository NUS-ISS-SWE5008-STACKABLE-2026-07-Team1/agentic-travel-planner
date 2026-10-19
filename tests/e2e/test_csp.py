"""The Content-Security-Policy, checked where it can actually be checked.

`tests/test_security_headers.py` asserts what the policy *says*. It cannot
assert what the policy *does*, because Flask's test client is not a browser: it
never parses the HTML, never fetches a stylesheet, and never refuses anything.
A policy that blanks every page would pass every unit test in this repo.

So these tests load each page in real Chromium and fail on any CSP violation
Chromium reports. That is a reliable signal: the browser logs every refusal to
the console as an error, naming the directive it violated.

Each test also asserts something *positive* about the page, not just the
absence of errors. A page that failed to load at all has no violations either,
and would otherwise pass.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

# Chromium's wording for a CSP refusal, across directives:
#   "Refused to load the stylesheet 'https://…' because it violates the
#    following Content Security Policy directive: …"
_VIOLATION = re.compile(
    r"Refused to|Content Security Policy|violates the following", re.IGNORECASE
)


def _watch_for_violations(page) -> list[str]:
    """Collect CSP complaints from the console and from uncaught page errors.

    Returns a list that fills as the page runs; read it *after* the page has
    settled. Both channels are watched because a blocked script surfaces as a
    console error while the knock-on failure ("Chart is not defined") surfaces
    as a page error, and the second is the more legible of the two.
    """
    violations: list[str] = []
    page.on(
        "console",
        lambda message: (
            violations.append(f"console[{message.type}]: {message.text}")
            if _VIOLATION.search(message.text)
            else None
        ),
    )
    page.on(
        "pageerror",
        lambda error: (
            violations.append(f"pageerror: {error}")
            if _VIOLATION.search(str(error))
            else None
        ),
    )
    return violations


def test_the_login_page_loads_with_no_csp_violation(page, live_server):
    """The first page anyone sees, and the one carrying the Bootstrap bundle."""
    violations = _watch_for_violations(page)
    page.goto(live_server.base_url)
    page.wait_for_load_state("networkidle")

    expect(page.locator("form")).to_be_visible()
    assert not violations, "CSP violations on the login page:\n  " + "\n  ".join(violations)


def test_the_brand_font_actually_loads(page, live_server):
    """The failure mode with no error message anywhere.

    `fonts.googleapis.com` reaches the page through an `@import` inside
    app.css, so it appears in no template. A CSP that lists only the origins
    visible in the templates blocks it, every page silently falls back to
    system-ui, and nothing in CI notices. This is the assertion that does.
    """
    page.goto(live_server.base_url)
    page.wait_for_load_state("networkidle")

    body_font = page.evaluate("getComputedStyle(document.body).fontFamily")
    assert "DM Sans" in body_font, (
        f"body font is {body_font!r} — the Google Fonts @import was blocked or failed"
    )


def test_the_planner_page_loads_with_no_csp_violation(page, live_server, authenticated_context):
    """The heaviest page: bundle JS, intake.js, both JSON data islands, and
    Bootstrap's `data:` SVG form controls."""
    page = authenticated_context.new_page()
    violations = _watch_for_violations(page)
    page.goto(f"{live_server.base_url}/main")
    page.wait_for_load_state("networkidle")

    expect(page.locator("form").first).to_be_visible()
    assert not violations, "CSP violations on /main:\n  " + "\n  ".join(violations)


def test_the_admin_dashboard_still_draws_its_chart(page, live_server, authenticated_context):
    """/admin is the only page loading chart.js, and until now the only page
    with no browser test — it answered 403 in this harness because
    `Config.ADMIN_EMAIL` is evaluated at import time (see `E2EConfig`).

    That gap mattered here specifically: Chart.js v2 injected a `<style>`
    element, which a strict `style-src` blocks. v4 is not believed to, and this
    is the test that stops that being a belief.
    """
    page = authenticated_context.new_page()
    violations = _watch_for_violations(page)
    # Mocked rather than live, following test_plan_rendering.py: deterministic,
    # and it gives the chart known data to draw.
    page.route(
        "**/api/v1/admin/activity",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"agents": [], "requests": [], "token_usage": []}',
        ),
    )
    page.goto(f"{live_server.base_url}/admin")
    page.wait_for_load_state("networkidle")

    assert page.evaluate("!!window.Chart"), "chart.js did not load — check script-src"
    assert not violations, "CSP violations on /admin:\n  " + "\n  ".join(violations)
