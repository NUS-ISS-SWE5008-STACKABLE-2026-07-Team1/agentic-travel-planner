"""Gap detection and answer merging. Pure functions: no model is involved."""

import pytest
from pydantic import ValidationError

from flaskapp.travel_ai.agents.orchestrator_agent.intake import (
    clarification_question, compute_gaps, merge_answers, merge_intents,
    to_request_payload,
)
from flaskapp.travel_ai.agents.orchestrator_agent.intake_schemas import ExtractedIntent
from flaskapp.travel_ai.schemas import TravelRequest

COMPLETE = {
    "origin": "Singapore", "destination": "Japan", "destination_city": "Tokyo",
    "plan_scope": "both",
    "departure_date": "2026-10-10", "return_date": "2026-10-24",
    "travellers": 2, "budget": 6000.0, "currency": "SGD",
    "traveller_ages": [34, 32], "traveller_genders": ["male", "female"],
    "traveller_accessibility_needs": [[], ["step-free access"]],
}


def keys(missing):
    return [field.key for field in missing]


def test_empty_intent_asks_for_every_scalar_field():
    missing = compute_gaps(ExtractedIntent())
    assert keys(missing) == [
        "plan_scope", "origin", "destination", "destination_city",
        "departure_date", "return_date", "travellers", "budget",
    ]


def test_per_traveller_questions_appear_only_once_party_size_is_known():
    assert not any(field.traveller_index is not None for field in compute_gaps(ExtractedIntent()))
    missing = compute_gaps(ExtractedIntent(travellers=2))
    assert "traveller_ages.0" in keys(missing)
    assert "traveller_genders.1" in keys(missing)
    assert "traveller_ages.2" not in keys(missing)


def test_known_age_is_not_asked_again_but_its_sibling_slot_is():
    missing = compute_gaps(ExtractedIntent(travellers=2, traveller_ages=[34, None]))
    assert "traveller_ages.0" not in keys(missing)
    assert "traveller_ages.1" in keys(missing)


def test_a_month_and_a_duration_are_not_dates():
    """The extractor may only record what was stated; this asserts the gap side."""
    missing = compute_gaps(ExtractedIntent(destination="Tokyo", travellers=2))
    assert "departure_date" in keys(missing)
    assert "return_date" in keys(missing)


def test_merge_is_monotonic_and_blank_answers_do_not_erase_known_values():
    extracted = ExtractedIntent(origin="Singapore", travellers=1)
    merged = merge_answers(extracted, {"origin": "", "budget": "3000"})
    assert merged.origin == "Singapore"
    assert merged.budget == 3000.0


def test_blank_accessibility_answer_means_none_and_closes_the_gap():
    extracted = ExtractedIntent(travellers=1)
    merged = merge_answers(extracted, {"traveller_accessibility_needs.0": ""})
    assert merged.traveller_accessibility_needs == [[]]
    assert "traveller_accessibility_needs.0" not in keys(compute_gaps(merged))


def test_unmentioned_accessibility_is_not_a_gap_and_defaults_to_none():
    without_accessibility = ExtractedIntent.model_validate({
        key: value for key, value in COMPLETE.items()
        if key != "traveller_accessibility_needs"
    })
    assert compute_gaps(without_accessibility) == []
    payload = to_request_payload(without_accessibility)
    assert payload["traveller_accessibility_needs"] == [[], []]
    assert TravelRequest.model_validate(payload).travellers == 2


def test_accessibility_answer_splits_on_commas():
    merged = merge_answers(
        ExtractedIntent(travellers=1),
        {"traveller_accessibility_needs.0": "step-free access, wheelchair-accessible room"},
    )
    assert merged.traveller_accessibility_needs == [
        ["step-free access", "wheelchair-accessible room"]
    ]


def test_lowering_traveller_count_truncates_per_traveller_lists():
    extracted = ExtractedIntent(travellers=3, traveller_ages=[34, 32, 8])
    merged = merge_answers(extracted, {"travellers": 2})
    assert merged.traveller_ages == [34, 32]
    assert len(merged.traveller_genders) == 2


def test_raising_traveller_count_adds_unanswered_slots():
    merged = merge_answers(ExtractedIntent(travellers=1, traveller_ages=[34]), {"travellers": 2})
    assert merged.traveller_ages == [34, None]
    assert "traveller_ages.1" in keys(compute_gaps(merged))


def test_answer_for_a_traveller_beyond_the_party_size_is_ignored():
    merged = merge_answers(ExtractedIntent(travellers=1), {"traveller_ages.5": 40})
    assert merged.traveller_ages == [None]


def test_unknown_answer_keys_are_ignored():
    merged = merge_answers(ExtractedIntent(travellers=1), {"nonsense": "x"})
    assert merged.travellers == 1


def test_invalid_answer_is_rejected_rather_than_silently_dropped():
    with pytest.raises(ValidationError):
        merge_answers(ExtractedIntent(travellers=1), {"budget": "not-a-number"})


def test_complete_intent_has_no_gaps_and_builds_an_accepted_request():
    extracted = ExtractedIntent.model_validate(COMPLETE)
    assert compute_gaps(extracted) == []
    payload = to_request_payload(extracted)
    request = TravelRequest.model_validate(payload)
    assert request.destination == "Japan"
    assert request.destination_city == "Tokyo"
    assert request.travellers == 2
    assert "Traveler 2: step-free access" in request.accessibility_needs


def test_payload_defaults_currency_and_risk_tolerance():
    payload = to_request_payload(ExtractedIntent.model_validate({**COMPLETE, "currency": None}))
    assert payload["currency"] == "SGD"
    assert payload["risk_tolerance"] == "medium"


def test_a_return_before_departure_is_asked_again_rather_than_sent_on():
    """An impossible date pair must not reach the planner.

    Reporting it as a gap keeps the traveller in the card with their other
    answers intact, instead of failing validation after the card is gone.
    """
    extracted = ExtractedIntent.model_validate({
        **COMPLETE, "departure_date": "2026-10-24", "return_date": "2026-10-10",
    })
    missing = compute_gaps(extracted)
    assert keys(missing) == ["return_date"]
    assert "on or after" in missing[0].hint


def test_same_day_return_is_accepted():
    extracted = ExtractedIntent.model_validate({
        **COMPLETE, "departure_date": "2026-10-10", "return_date": "2026-10-10",
    })
    assert compute_gaps(extracted) == []


def test_chat_turns_merge_and_latest_explicit_fact_can_correct_an_answer():
    current = ExtractedIntent(origin="Singapore", destination="Tokyo", travellers=2)
    update = ExtractedIntent(destination="Osaka", budget=5000)
    merged = merge_intents(current, update)
    assert merged.origin == "Singapore"
    assert merged.destination == "Osaka"
    assert merged.budget == 5000
    assert len(merged.traveller_ages) == 2


def test_clarification_question_names_the_deterministic_gaps():
    question = clarification_question(compute_gaps(ExtractedIntent(destination="Tokyo")))
    assert "flying from" in question.lower()
    assert "departure date" in question.lower()
