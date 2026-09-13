"""Intake carries the traveller's scope through to the request.

Nothing is inferred: the extraction records a scope the traveller stated, and
when they did not state one the card asks. An unanswered scope is `both`, the
superset, so the failure mode is running an agent nobody needed rather than
silently dropping one they did.
"""

from flaskapp.travel_ai.agents.orchestrator_agent.intake import (
    compute_gaps, to_request_payload,
)
from flaskapp.travel_ai.agents.orchestrator_agent.intake_schemas import ExtractedIntent
from flaskapp.travel_ai.dispatch import specialists_for
from flaskapp.travel_ai.schemas import TravelRequest

COMPLETE = {
    "origin": "Singapore", "destination": "Japan", "destination_city": "Tokyo",
    "plan_scope": "hotel",
    "departure_date": "2026-10-10", "return_date": "2026-10-24",
    "travellers": 1, "budget": 3000.0, "currency": "SGD",
    "traveller_ages": [34], "traveller_genders": ["male"],
    "traveller_accessibility_needs": [[]],
}


def keys(missing):
    return [field.key for field in missing]


def test_scope_is_asked_when_the_traveller_did_not_state_it():
    assert "plan_scope" in keys(compute_gaps(ExtractedIntent()))


def test_scope_is_not_asked_once_it_is_known():
    intent = ExtractedIntent.model_validate(COMPLETE)
    assert "plan_scope" not in keys(compute_gaps(intent))


def test_the_scope_question_renders_as_a_select():
    field = next(f for f in compute_gaps(ExtractedIntent()) if f.key == "plan_scope")
    assert field.input == "scope"


def test_the_scope_reaches_the_request_and_the_dispatch():
    payload = to_request_payload(ExtractedIntent.model_validate(COMPLETE))
    assert payload["plan_scope"] == "hotel"
    selected = specialists_for(TravelRequest.model_validate(payload))
    assert "flight_agent" not in selected
    assert "hotel_transport_agent" in selected


def test_an_unanswered_scope_falls_back_to_the_superset():
    payload = to_request_payload(
        ExtractedIntent.model_validate({**COMPLETE, "plan_scope": None})
    )
    assert payload["plan_scope"] == "both"
    assert len(specialists_for(TravelRequest.model_validate(payload))) == 4


def test_hotel_only_does_not_ask_where_you_are_flying_from():
    """Reported: a hotel-only trip still asked "Flying from", which is not a
    question a stay has an answer to."""
    gaps = keys(compute_gaps(ExtractedIntent(plan_scope="hotel")))
    assert "origin" not in gaps


def test_hotel_only_still_asks_for_the_destination():
    """`find_city` is scoped by country, so hotel search needs both."""
    gaps = keys(compute_gaps(ExtractedIntent(plan_scope="hotel")))
    assert "destination" in gaps and "destination_city" in gaps


def test_every_other_scope_still_asks_where_you_are_flying_from():
    for scope in ("both", "flights"):
        assert "origin" in keys(compute_gaps(ExtractedIntent(plan_scope=scope)))


def test_a_hotel_only_brief_completes_without_an_origin():
    intent = ExtractedIntent.model_validate({
        **COMPLETE, "plan_scope": "hotel", "origin": None,
    })
    assert compute_gaps(intent) == []
    payload = to_request_payload(intent)
    assert payload["origin"] is None
    assert TravelRequest.model_validate(payload).origin is None


def test_the_clarification_sentence_has_no_question_mark_in_the_middle():
    """The scope label is interpolated into a list of things to share.

    A label ending in "?" produced: "could you share what should we plan?,
    flying from, ..." — the sentence terminates and then keeps going.
    """
    from flaskapp.travel_ai.agents.orchestrator_agent.intake import clarification_question

    sentence = clarification_question(compute_gaps(ExtractedIntent()))
    assert sentence.endswith("?")
    assert "?" not in sentence[:-1], sentence


def test_the_hotel_scope_label_says_transport_too():
    """That agent books accommodation AND airport transfers and local transport.

    Calling the option "Hotel only" understates what the traveller gets, and a
    traveller who needs an airport transfer might reasonably not pick it.
    """
    from pathlib import Path

    javascript = Path("flaskapp/static/js/intake.js").read_text(encoding="utf-8")
    # Match the option tuple, not a bare substring: prose and comments mention
    # the old label, and a whole-file search would flag those too.
    assert '["hotel", "Hotel and transport only"]' in javascript
    assert '["hotel", "Hotel only"]' not in javascript


# --- the structured form, not just the conversational card -------------------


def test_the_form_offers_the_same_three_choices():
    """The feature was only reachable through the chat intake.

    Every form submission omitted `plan_scope` and so defaulted to `both`,
    silently dispatching all four specialists however the traveller filled it in.
    """
    from pathlib import Path

    html = Path("flaskapp/templates/main.html").read_text(encoding="utf-8")
    assert 'name="plan_scope"' in html
    assert 'value="flights"' in html and 'value="hotel"' in html and 'value="both"' in html


def test_the_form_payload_carries_the_scope():
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    assert 'plan_scope: data.get("plan_scope")' in javascript


def test_choosing_hotel_only_releases_the_required_origin():
    """`origin` is `required` on the form, so without this a hotel-only
    submission cannot pass browser validation at all — the traveller is asked
    for a departure country the request no longer has a field for."""
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    assert "applyScope" in javascript
    assert "originFieldset" in javascript


def test_going_back_restores_the_scope_and_reapplies_it():
    """`restoreStoredTrip` runs after the initial `applyScope`, so a restored
    hotel-only trip would otherwise come back with the scope reset to `both`
    and the origin fields required again."""
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    assert 'setValue("plan_scope", payload.plan_scope)' in javascript
    restore = javascript[javascript.index("const restoreStoredTrip"):]
    body = restore[:restore.index("\n  };")]
    assert "applyScope()" in body, "restore must re-apply the scope it just set"


# --- the scope choice as option buttons rather than a dropdown ---------------


def test_the_form_offers_scope_as_option_buttons():
    from pathlib import Path

    html = Path("flaskapp/templates/main.html").read_text(encoding="utf-8")
    assert html.count('type="radio"') >= 3
    assert 'name="plan_scope"' in html
    assert '<select class="form-select" id="plan_scope"' not in html


def test_restoring_a_trip_checks_the_right_option_button():
    """`setValue` assigns `.value`, which on a radio sets its value attribute
    rather than selecting it — the group would stay on its default."""
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    assert 'type === "radio"' in javascript


def test_applying_scope_reads_the_checked_option_button():
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    assert "[name='plan_scope']:checked" in javascript


def test_the_card_renders_scope_as_option_buttons_and_reads_only_the_checked_one():
    """Every created control carries data-key, and the collector reads `.value`
    from each — so without a guard all three radios answer and the last wins."""
    from pathlib import Path

    javascript = Path("flaskapp/static/js/intake.js").read_text(encoding="utf-8")
    assert 'field.input === "scope"' in javascript
    assert 'input.type === "radio" && !input.checked' in javascript
