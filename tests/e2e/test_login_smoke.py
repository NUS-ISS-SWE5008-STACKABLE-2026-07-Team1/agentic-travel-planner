"""The one real, full-stack smoke test: login -> submit a trip -> a plan renders.

Real browser, real Flask server (`live_server`), real login form, real
LangGraph run against the stub LLM provider (`scripts/ci_stub_provider.py`,
the same one `ci-dast.yml` uses). No `/api/v1/*` route is mocked here — that
tier lives in `test_plan_rendering.py`. This test exists to prove the pipeline
still wires together end to end, not to cover every rendering detail.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

# The demo account every fresh database gets for free (see `conftest.py`).
DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "TravelDemo2026!"

# Singapore -> Tokyo is the pair already exercised by tests/test_api.py, so a
# failure here means something changed in the live wiring, not in an untested
# route/city combination. Dates fall inside the seed flight inventory's window
# (2026-08-24 to 2027-01-06, per CLAUDE.md).
DEPARTURE_DATE = "2026-10-15"
RETURN_DATE = "2026-10-22"


def test_login_and_plan_render(page, live_server):
    page.goto(live_server.base_url + "/")

    page.fill("#email", DEMO_EMAIL)
    page.fill("#password", DEMO_PASSWORD)
    page.click("#submit")
    expect(page).to_have_url(live_server.base_url + "/main")

    page.click("#manual-planner summary")
    page.select_option("#origin", "Singapore")
    page.select_option("#origin_city", "Singapore")
    page.select_option("#destination", "Japan")
    page.select_option("#destination_city", "Tokyo")
    page.fill("#departure_date", DEPARTURE_DATE)
    page.fill("#return_date", RETURN_DATE)
    page.fill("#budget", "3000")
    # renderTravelerFields() already drew one traveler card for the default
    # travellers=1 by the time the form is visible.
    page.fill("#traveller-age-0", "30")
    page.select_option("#traveller-gender-0", "female")

    page.click("#travel-plan-form button[type='submit']")
    page.wait_for_url("**/chat/*")
    expect(page.locator("#job-status")).to_have_text(
        "All agents completed their work.", timeout=30_000
    )
    expect(page.locator("#plan-navigation")).not_to_have_class("d-none")
