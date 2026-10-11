"""Relative dates, and the guard that stops a wrong one shipping silently.

The prompt never told the model what day it was, so "tomorrow" was
unanswerable. Asked anyway, gpt-4.1-mini returned 2024-06-13 on 2026-10-09 —
a date near its training cutoff, over two years in the past.

Nothing caught it. `TravelRequest` only checks that the return is not before
the departure, so a 2024 trip validated, matched zero seed inventory (which
starts 2026-08-24), fell to the prompt-only path with its options stripped, and
produced a vague plan that never told the traveller their date was the problem.

So two things: the model is given today's date, and a past date is refused
regardless. The anchor makes a right answer likely; the guard makes a wrong one
impossible to ship.
"""

from datetime import date, timedelta

from flaskapp.travel_ai.agents.orchestrator_agent.intake import (
    compute_gaps, drop_impossible_dates, intake_instruction,
)
from flaskapp.travel_ai.agents.orchestrator_agent.intake_schemas import ExtractedIntent

TODAY = date(2026, 10, 9)


# --- the anchor -------------------------------------------------------------

def test_the_instruction_states_todays_date():
    text = intake_instruction(today=TODAY)
    assert "2026-10-09" in text


def test_the_instruction_says_to_resolve_relative_dates_against_it():
    text = intake_instruction(today=TODAY).lower()
    assert "tomorrow" in text
    assert "relative" in text


def test_the_anchor_defaults_to_the_real_today():
    assert date.today().isoformat() in intake_instruction()


def test_the_rest_of_the_instruction_is_unchanged():
    """The anchor is added, not substituted for the existing rules."""
    text = intake_instruction(today=TODAY)
    assert "emit null for anything the traveller did not state" in text
    assert "`destination` is the COUNTRY" in text


# --- the guard --------------------------------------------------------------

def test_a_past_departure_date_is_refused():
    intent = ExtractedIntent(departure_date=date(2024, 6, 13))
    assert drop_impossible_dates(intent, today=TODAY).departure_date is None


def test_a_past_return_date_is_refused():
    intent = ExtractedIntent(return_date=date(2024, 6, 21))
    assert drop_impossible_dates(intent, today=TODAY).return_date is None


def test_today_itself_is_allowed():
    """Someone booking a flight for this evening is not making a mistake."""
    intent = ExtractedIntent(departure_date=TODAY)
    assert drop_impossible_dates(intent, today=TODAY).departure_date == TODAY


def test_a_future_date_is_left_alone():
    soon = TODAY + timedelta(days=1)
    intent = ExtractedIntent(departure_date=soon, return_date=TODAY + timedelta(days=6))
    out = drop_impossible_dates(intent, today=TODAY)
    assert out.departure_date == soon
    assert out.return_date == TODAY + timedelta(days=6)


def test_the_guard_does_not_mutate_what_it_was_given():
    intent = ExtractedIntent(departure_date=date(2024, 6, 13))
    drop_impossible_dates(intent, today=TODAY)
    assert intent.departure_date == date(2024, 6, 13)


def test_an_intent_with_no_dates_is_returned_unchanged():
    intent = ExtractedIntent(destination_city="Tokyo")
    assert drop_impossible_dates(intent, today=TODAY) is intent


# --- what the traveller sees ------------------------------------------------

def test_a_refused_date_becomes_a_question_again():
    """The point of the guard: a hallucinated date turns back into a gap, so
    the traveller is asked rather than planned around."""
    intent = drop_impossible_dates(
        ExtractedIntent(departure_date=date(2024, 6, 13), return_date=date(2024, 6, 21)),
        today=TODAY,
    )
    asked = {f.name for f in compute_gaps(intent)}
    assert "departure_date" in asked
    assert "return_date" in asked


# --- saying WHY the date was refused ----------------------------------------
#
# Refusing silently was the whole complaint. A traveller who writes "departing
# 2026-08-28" on 2026-10-11 gave a real date, clearly; being asked for it again
# with no explanation reads as the system having ignored them, not as their
# date having passed.

def test_past_dates_are_reported_not_just_dropped():
    from flaskapp.travel_ai.agents.orchestrator_agent.intake import past_dates

    intent = ExtractedIntent(departure_date=date(2026, 8, 28),
                             return_date=date(2026, 9, 2))
    assert past_dates(intent, today=TODAY) == {
        "departure_date": date(2026, 8, 28),
        "return_date": date(2026, 9, 2),
    }


def test_a_future_date_is_not_reported_as_past():
    from flaskapp.travel_ai.agents.orchestrator_agent.intake import past_dates

    intent = ExtractedIntent(departure_date=TODAY + timedelta(days=5))
    assert past_dates(intent, today=TODAY) == {}


def test_the_question_explains_that_the_date_has_passed():
    intent = ExtractedIntent(departure_date=date(2026, 8, 28),
                             return_date=date(2026, 9, 2))
    from flaskapp.travel_ai.agents.orchestrator_agent.intake import past_dates

    gone = past_dates(intent, today=TODAY)
    gaps = compute_gaps(drop_impossible_dates(intent, today=TODAY), past=gone)
    hints = {f.name: f.hint for f in gaps}

    assert "2026-08-28" in hints["departure_date"]
    assert "2026-10-11" in hints["departure_date"], "says what today is, so the gap is obvious"
    assert "2026-09-02" in hints["return_date"]


def test_an_ordinary_gap_keeps_its_own_hint():
    """The past-date hint replaces nothing else: a budget field still explains
    itself."""
    gaps = compute_gaps(ExtractedIntent(), past=None)
    hints = {f.name: f.hint for f in gaps}
    assert hints["budget"] and "Total for the trip" in hints["budget"]
    assert hints["departure_date"] is None, "not past, just unstated"


def test_the_past_hint_is_only_attached_to_the_field_it_belongs_to():
    intent = ExtractedIntent(departure_date=date(2026, 8, 28),
                             return_date=TODAY + timedelta(days=20))
    from flaskapp.travel_ai.agents.orchestrator_agent.intake import past_dates

    gone = past_dates(intent, today=TODAY)
    gaps = compute_gaps(drop_impossible_dates(intent, today=TODAY), past=gone)
    hints = {f.name: f.hint for f in gaps}
    assert "2026-08-28" in hints["departure_date"]
    assert "return_date" not in hints, "a valid return date is not a gap at all"
