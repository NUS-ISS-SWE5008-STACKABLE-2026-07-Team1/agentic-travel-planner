"""The live password checklist on /register and /forgot-password.

The bug this pins: after a submit that failed on the password, Bootstrap's
server-rendered red error stayed on screen while the traveller typed a
password that met every rule, so a valid password still looked rejected. It
only went away on the next submit.

Two things are checked in a real browser:

* **The stale error clears** once the field is fixed, on both pages.
* **The browser agrees with the server.** password-rules.js mirrors
  `strong_password` in flaskapp/forms.py; if the two ever drift, the checklist
  shows "all done" for a password the server rejects, or the reverse. Each
  sample below is run through both and the verdicts compared.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect
from wtforms.validators import ValidationError

from flaskapp.forms import strong_password

pytestmark = pytest.mark.e2e

VALID = "Travel-2026!x"

# Edge cases where a naive JavaScript copy of the rule would disagree with
# Python: code points vs UTF-16 length, Unicode digits, non-ASCII letters.
SAMPLES = [
    "Travel-2026!",          # exactly 12, all rules
    "Travel_2026x",          # underscore is special
    "travel 2026 X",         # a space is special
    "Pässwörd2026x",         # accented letters count as special, not lower
    "TRAVEL-2026!",          # no lowercase
    "travel-2026!",          # no uppercase
    "Travel-Journey!",       # no digit
    "Travel2026abc",         # no special character
    "Trav-2026!",            # too short
    "Travel-٢٠٢٦!",          # Arabic-Indic digits: Python's \d accepts them
    "Tr-2026!😀😀😀😀",       # 12 code points, 16 UTF-16 units
    "Tr-2026!😀😀😀",         # 11 code points, 14 UTF-16 units: too short
]


def _server_accepts(password: str) -> bool:
    class Field:
        data = password

    try:
        strong_password(None, Field())
    except ValidationError:
        return False
    return True


def _browser_accepts(page) -> bool:
    items = page.locator("#password-help [data-rule]")
    return items.count() == 5 and all(
        "is-met" in (items.nth(i).get_attribute("class") or "")
        for i in range(items.count())
    )


def test_checklist_verdict_matches_the_server(page, live_server):
    page.goto(live_server.base_url + "/register")
    for password in SAMPLES:
        page.fill("#password", password)
        assert _browser_accepts(page) == _server_accepts(password), (
            f"{password!r}: the checklist and strong_password disagree — "
            "update static/js/password-rules.js to match flaskapp/forms.py"
        )


def test_stale_password_error_clears_once_the_password_is_valid(page, live_server):
    page.goto(live_server.base_url + "/register")
    page.fill("#name", "Browser Tester")
    page.fill("#email", "stale-error@example.com")
    page.select_option("#country", "Singapore")
    page.fill("#birthday", "1990-05-01")
    page.fill("#password", "weak")
    page.fill("#confirm_password", "different")
    page.click("#submit")

    password = page.locator("#password")
    confirm = page.locator("#confirm_password")
    assert "is-invalid" in (password.get_attribute("class") or ""), "server error not rendered"
    assert "is-invalid" in (confirm.get_attribute("class") or "")

    # Still too short: the server's error must stay.
    page.fill("#password", "Travel-20")
    assert "is-invalid" in (password.get_attribute("class") or "")

    # Every rule met: the stale error goes, without submitting.
    page.fill("#password", VALID)
    assert "is-invalid" not in (password.get_attribute("class") or "")
    expect(page.locator("#password ~ .invalid-feedback")).to_be_hidden()

    # The confirm error clears only once it actually matches.
    page.fill("#confirm_password", VALID[:-1])
    assert "is-invalid" in (confirm.get_attribute("class") or "")
    page.fill("#confirm_password", VALID)
    assert "is-invalid" not in (confirm.get_attribute("class") or "")


def test_reset_page_has_the_same_live_checklist(page, live_server):
    page.goto(live_server.base_url + "/forgot-password")
    page.fill("#email", live_server.login_email)
    page.fill("#password", "weak")
    page.fill("#confirm_password", "weak")
    page.click("#submit")

    password = page.locator("#password")
    assert "is-invalid" in (password.get_attribute("class") or "")
    page.fill("#password", VALID)
    assert _browser_accepts(page)
    assert "is-invalid" not in (password.get_attribute("class") or "")
