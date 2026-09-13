"""Frontend-only coverage: DOM rendering and the poll loop, `/api/v1/*` mocked.

No LLM, no real plan pipeline — `page.route()` intercepts every API call
before it leaves the browser, so this is fast and deterministic and exercises
exactly what `app.js` owns: building the request payload from the manual
form, the status poll loop, and `renderPlan()`. `test_login_smoke.py` covers
the one real end-to-end path; this file is where per-field rendering
coverage belongs.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

MOCK_REQUEST_ID = "11111111-1111-1111-1111-111111111111"

# Shape matches `flaskapp/travel_ai/schemas.py`'s `PlanResponse`/`TravelPlan` —
# renderPlan() reads these fields directly, so a mock missing one would fail
# with a JS error rather than a meaningful assertion.
MOCK_PLAN_RESPONSE = {
    "request_id": MOCK_REQUEST_ID,
    "status": "completed",
    "plan": {
        "title": "Five days in Tokyo",
        "summary": "A mocked plan for the frontend poll-loop test.",
        "itinerary": ["Day 1: Arrive in Tokyo", "Day 2: Explore Shibuya"],
        "estimated_total_cost": 2200.0,
        "currency": "SGD",
        "rationale": ["Mocked rationale line"],
        "alternatives": [],
        "sources": [],
        "assumptions": [],
        "limitations": [],
        "safety": {"passed": True, "checks": ["mock-check"], "warnings": []},
    },
    "agent_findings": [],
    "trace_url": f"/api/v1/traces/{MOCK_REQUEST_ID}",
}


def test_submitted_trip_renders_after_polling(live_server, authenticated_context):
    page = authenticated_context.new_page()

    page.route(
        re.compile(r"/api/v1/travel-plans$"),
        lambda route: route.fulfill(
            status=202,
            json={
                "request_id": MOCK_REQUEST_ID,
                "status": "queued",
                "chat_url": f"/chat/{MOCK_REQUEST_ID}",
            },
        ),
    )
    page.route(
        re.compile(rf"/api/v1/travel-plans/{MOCK_REQUEST_ID}/status$"),
        lambda route: route.fulfill(json={
            "request_id": MOCK_REQUEST_ID,
            "status": "completed",
            # `get_travel_plan_status` nests the plan under "response" —
            # `poll()` in app.js reads `job.response`, not the body itself.
            "response": MOCK_PLAN_RESPONSE,
        }),
    )
    page.route(
        re.compile(rf"/api/v1/traces/{MOCK_REQUEST_ID}$"),
        lambda route: route.fulfill(json={"events": []}),
    )

    page.goto(live_server.base_url + "/main")
    page.click("#manual-planner summary")
    page.select_option("#origin", "Singapore")
    page.select_option("#origin_city", "Singapore")
    page.select_option("#destination", "Japan")
    page.select_option("#destination_city", "Tokyo")
    page.fill("#departure_date", "2026-10-15")
    page.fill("#return_date", "2026-10-22")
    page.fill("#budget", "3000")
    page.fill("#traveller-age-0", "30")
    page.select_option("#traveller-gender-0", "female")

    page.click("#travel-plan-form button[type='submit']")
    page.wait_for_url(f"**/chat/{MOCK_REQUEST_ID}")

    expect(page.locator("#job-status")).to_have_text(
        "All agents completed their work.", timeout=10_000
    )
    expect(page.locator("#plan-content")).to_contain_text(MOCK_PLAN_RESPONSE["plan"]["title"])
    expect(page.locator("#plan-content")).to_contain_text("Day 1: Arrive in Tokyo")
    expect(page.locator("#plan-navigation")).not_to_have_class(re.compile("d-none"))
