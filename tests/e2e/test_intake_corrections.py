"""The chat intake shows a correction, not just the name of a field.

Pins #64: when an answer the traveller already gave is wrong (a departure date
in the past), the progress panel under the chat must say *what* is wrong, set
the chip apart from fields not yet reached, and announce it to screen readers.
Before #64 the traveller saw a bare "Departure date" chip and no reason.

`/api/v1/travel-intents` is mocked, as in test_plan_rendering.py, so this pins
the rendering in intake.js, not the model's extraction.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

HINT = "2026-08-28 has already passed. Enter a date from today onwards."

INTENT_RESPONSE = {
    "complete": False,
    "question": "Which day would you like to leave instead?",
    "extracted": {},
    "missing": [
        {"name": "departure_date", "label": "Departure date", "input": "date",
         "traveller_index": None, "hint": HINT, "problem": True},
        {"name": "budget", "label": "Budget", "input": "number",
         "traveller_index": None, "hint": "Leave blank if none", "problem": False},
        {"name": "traveller_ages", "label": "Age", "input": "number",
         "traveller_index": 0, "hint": None, "problem": False},
    ],
    "request": None,
    "request_id": "22222222-2222-2222-2222-222222222222",
}


def test_a_past_date_is_explained_in_the_chat(live_server, authenticated_context):
    page = authenticated_context.new_page()
    page.route("**/api/v1/travel-intents", lambda route: route.fulfill(json=INTENT_RESPONSE))

    # The main page hands the prompt to the chat intake, which submits it.
    page.goto(live_server.base_url + "/main")
    page.fill("#intent-prompt", "Tokyo from Singapore, departing 2026-08-28")
    page.click("#intent-form button[type='submit']")
    page.wait_for_url("**/chat/intake")

    progress = page.locator(".intake-progress")
    expect(progress.locator(".intake-progress-title")).to_have_text("3 details still needed")

    # Only the corrected field is set apart; a field not yet reached is not.
    expect(progress.locator(".intake-gap-chip-problem")).to_have_text("Departure date")
    expect(progress.locator(".intake-gap-chips span")).to_have_text(
        ["Departure date", "Budget", "Traveller 1: Age"]
    )

    # The correction is spelled out and announced; guidance hints are not.
    problems = progress.locator(".intake-progress-problems")
    expect(problems).to_have_attribute("role", "alert")
    expect(problems.locator("div")).to_have_text([f"Departure date: {HINT}"])

    expect(page.locator("#intent-conversation")).to_contain_text(INTENT_RESPONSE["question"])
