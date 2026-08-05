"""The multi-gap relaxation scenario (update.md §5, "give one example where Flight
Agent needs the LLM instead of being deterministic").

Fixture: a leg where every surviving flight is BOTH a red-eye (departs 22:30+) AND
arrives after the traveller's stated soft arrival preference — two valid relaxation
gaps exist simultaneously, and `PreferenceRelaxation` only allows proposing one. Built
once here so both the mocked tests below and `scripts/demo_multi_gap_relaxation.py`
(a real-LLM comparison) share the identical data — single source of truth, no drift
between what's tested and what's demoed.

What this file CAN prove: the mechanism handles either valid relaxation choice
correctly, for both a family and a solo-traveller context, and the deterministic gap
detection itself expresses no preference between the two gaps (it hands the LLM a
genuinely open choice). What this file CANNOT prove: that an LLM's actual choice is
context-sensitive — every response below is authored by the test, not a model.

**That claim WAS tested for real, 2026-07-20**, via `scripts/demo_multi_gap_relaxation.py`
against gpt-4.1-mini: family relaxed `soft_arrival_preference` (kept avoiding the
red-eye), solo relaxed `avoid_red_eye` instead (kept the arrival-time preference) —
different choices, both citing party composition in the rationale. See `update.md`
2026-07-20 for the full result and the one prompt change (`prompts.py`, weigh `party`
when multiple gaps exist) that produced it — the first run, before that instruction
existed, chose the same relaxation both times.
"""

from unittest.mock import Mock

import pytest

from flaskapp.travel_ai.agents.flight_agent.reasoning import run_flight_agent
from flaskapp.travel_ai.agents.flight_agent.domain import flight_preference_gaps
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    ArrivalPreference,
    FlightAgentResponse,
    FlightInventoryItem,
    FlightPreferences,
    FlightProposalRequest,
    PreferenceRelaxation,
    TripContext,
)

BASE_CONTEXT = dict(
    origin_airport="SIN",
    dest_airport="NRT",
    dest_city="Tokyo",
    dest_country="JP",
    depart_date="2026-09-01",
    return_date="2026-09-05",
    passport_country="SG",
    budget_total=6000,
)

# Soft preference: arrive by 10:00 on 2 Sep (destination-local). Both outbound
# candidates below depart late (red-eye by departure hour) AND land well after
# that, on the following day — so BOTH gaps are genuinely unsatisfiable by
# anything in this inventory, not just one of them.
DUAL_GAP_PREFS = FlightPreferences(
    avoid_red_eye=True,
    arrival_preferences=[
        ArrivalPreference(direction="OUTBOUND", by="2026-09-02T10:00+09:00", hard=False),
    ],
)

DGAP1 = FlightInventoryItem(
    flight_id="DGAP1-20260901", carrier="SQ", flight_no="DGAP1",
    origin_airport="SIN", dest_airport="NRT",
    dep_ts="2026-09-01T22:30+08:00", arr_ts="2026-09-02T14:30+09:00",
    duration_min=900, price=420, cabin_class="ECONOMY", seats_available=10,
    stops=0, wheelchair_assist_available=True, step_free_boarding=True,
)
DGAP2 = FlightInventoryItem(
    flight_id="DGAP2-20260901", carrier="SQ", flight_no="DGAP2",
    origin_airport="SIN", dest_airport="NRT",
    dep_ts="2026-09-01T23:10+08:00", arr_ts="2026-09-02T15:05+09:00",
    duration_min=915, price=580, cabin_class="ECONOMY", seats_available=10,
    stops=0, wheelchair_assist_available=True, step_free_boarding=True,
)
RETURN_LEG = FlightInventoryItem(
    flight_id="DGAP-RET-20260905", carrier="SQ", flight_no="DGAPRET",
    origin_airport="NRT", dest_airport="SIN",
    dep_ts="2026-09-05T11:00+09:00", arr_ts="2026-09-05T17:00+08:00",
    duration_min=400, price=400, cabin_class="ECONOMY", seats_available=10,
    stops=0, wheelchair_assist_available=True, step_free_boarding=True,
)
DUAL_GAP_INVENTORY = [DGAP1, DGAP2, RETURN_LEG]


def family_request() -> FlightProposalRequest:
    """A family with two young children — plausibly better served relaxing the
    arrival-time preference (land a bit later, in daylight) than putting kids
    through a red-eye."""
    return FlightProposalRequest(
        trip_context=TripContext(
            **BASE_CONTEXT, party={"adults": 2, "children": 2}, flight_preferences=DUAL_GAP_PREFS
        )
    )


def solo_business_request() -> FlightProposalRequest:
    """A solo traveller — plausibly better served keeping the red-eye (arrive
    already, use the day) and relaxing the arrival-time preference instead, or
    the reverse — the point is it's genuinely not obviously the same answer as
    the family case, and nothing in the deterministic layer decides it."""
    return FlightProposalRequest(
        trip_context=TripContext(
            **BASE_CONTEXT, party={"adults": 1, "children": 0}, flight_preferences=DUAL_GAP_PREFS
        )
    )


def _fake_llm(response: FlightAgentResponse) -> Mock:
    structured_llm = Mock()
    structured_llm.invoke.return_value = response
    llm = Mock()
    llm.with_structured_output.return_value = structured_llm
    return llm


# --- The deterministic layer hands the LLM a genuinely open choice ---

@pytest.mark.parametrize("build_request", [family_request, solo_business_request])
def test_both_gaps_exist_regardless_of_party(build_request):
    """Confirms the scenario is real for both contexts, and that gap detection
    itself is party-agnostic — it doesn't pre-decide anything; the choice is
    left entirely open for whatever reasons next."""
    gaps = flight_preference_gaps(build_request(), DUAL_GAP_INVENTORY)
    assert set(gaps["OUTBOUND"]) == {"avoid_red_eye", "soft_arrival_preference"}


# --- The mechanism accepts either valid choice symmetrically (no built-in bias) ---

@pytest.mark.parametrize("build_request", [family_request, solo_business_request])
def test_mechanism_accepts_avoid_red_eye_choice(build_request):
    response = FlightAgentResponse(
        rationale="Proposing to relax red-eye avoidance to open up options.",
        highlighted_flight_ids=["DGAP1-20260901"],
        proposed_relaxation=PreferenceRelaxation(field="avoid_red_eye", reason="both gaps present"),
        confidence=0.6,
    )
    llm = _fake_llm(response)
    proposal, result = run_flight_agent(build_request(), DUAL_GAP_INVENTORY, llm)
    assert result.relaxation_applied is not None
    assert result.relaxation_applied.field == "avoid_red_eye"


@pytest.mark.parametrize("build_request", [family_request, solo_business_request])
def test_mechanism_accepts_soft_arrival_preference_choice(build_request):
    response = FlightAgentResponse(
        rationale="Proposing to relax the arrival-time preference instead.",
        highlighted_flight_ids=["DGAP1-20260901"],
        proposed_relaxation=PreferenceRelaxation(
            field="soft_arrival_preference", direction="OUTBOUND", reason="both gaps present"
        ),
        confidence=0.6,
    )
    llm = _fake_llm(response)
    proposal, result = run_flight_agent(build_request(), DUAL_GAP_INVENTORY, llm)
    assert result.relaxation_applied is not None
    assert result.relaxation_applied.field == "soft_arrival_preference"


def test_fixture_is_symmetric_between_the_two_party_contexts():
    """Explicit check that family_request() and solo_business_request() differ
    ONLY in party — same inventory, same preferences, same everything else —
    so any difference in a real model's behaviour would be attributable to
    party context and nothing else."""
    family = family_request().trip_context
    solo = solo_business_request().trip_context
    assert family.party != solo.party
    family_dict = family.model_dump(exclude={"party"})
    solo_dict = solo.model_dump(exclude={"party"})
    assert family_dict == solo_dict
