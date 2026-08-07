"""Option B — bounded soft-preference relaxation (discussion_agents_vs_deterministic.md §1).

Two layers under test:
- domain.py: gap detection + validation + application (pure, no LLM).
- agent.py: the LLM proposes a relaxation, code re-verifies and applies at
  most one extra tool+LLM pass. Fake ChatModel, no real API call.
"""

from unittest.mock import Mock

from flaskapp.travel_ai.agents.flight_agent.reasoning import run_flight_agent
from flaskapp.travel_ai.agents.flight_agent.domain import (
    apply_relaxation,
    flight_preference_gaps,
    preference_gap_for_leg,
    propose_flights,
    relaxation_is_valid,
)
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
    party={"adults": 1, "children": 0},
    passport_country="SG",
)


def _flight(flight_id, dep_ts, arr_ts, price, stops=0):
    return FlightInventoryItem(
        flight_id=flight_id,
        carrier="SQ",
        flight_no=flight_id,
        origin_airport="SIN",
        dest_airport="NRT",
        dep_ts=dep_ts,
        arr_ts=arr_ts,
        duration_min=400,
        price=price,
        cabin_class="ECONOMY",
        seats_available=10,
        stops=stops,
        wheelchair_assist_available=True,
        step_free_boarding=True,
    )


# Both outbound options are red-eyes — so avoid_red_eye can't be satisfied by
# any surviving flight: a genuine, relaxable gap.
RE1 = _flight("RE1", "2026-09-01T23:00+08:00", "2026-09-02T05:00+09:00", 300)
RE2 = _flight("RE2", "2026-09-01T22:30+08:00", "2026-09-02T04:30+09:00", 350)
# A return leg with a normal daytime flight so RETURN has no gap.
RET = FlightInventoryItem(
    flight_id="RET1", carrier="SQ", flight_no="RET1",
    origin_airport="NRT", dest_airport="SIN",
    dep_ts="2026-09-05T11:00+09:00", arr_ts="2026-09-05T17:00+08:00",
    duration_min=400, price=400, cabin_class="ECONOMY", seats_available=10,
    stops=0, wheelchair_assist_available=True, step_free_boarding=True,
)
INVENTORY = [RE1, RE2, RET]


def _request(prefs: FlightPreferences) -> FlightProposalRequest:
    return FlightProposalRequest(trip_context=TripContext(**BASE_CONTEXT, flight_preferences=prefs))


def _fake_llm(*responses) -> Mock:
    structured_llm = Mock()
    structured_llm.invoke.side_effect = list(responses)
    llm = Mock()
    llm.with_structured_output.return_value = structured_llm
    return llm, structured_llm


# --- domain-level (pure) ---

def test_gap_detected_when_all_survivors_violate_soft_pref():
    gaps = flight_preference_gaps(_request(FlightPreferences(avoid_red_eye=True)), INVENTORY)
    assert "avoid_red_eye" in gaps["OUTBOUND"]
    assert gaps["RETURN"] == []  # daytime return has no gap


def test_no_gap_when_some_survivor_satisfies_the_pref():
    daytime = _flight("DAY1", "2026-09-01T09:00+08:00", "2026-09-01T16:00+09:00", 500)
    gaps = flight_preference_gaps(_request(FlightPreferences(avoid_red_eye=True)), [RE1, RE2, daytime, RET])
    assert "avoid_red_eye" not in gaps["OUTBOUND"]


def test_empty_leg_produces_no_gap_hard_constraint_not_relaxable():
    # max_stops=0 is a hard filter; combined with an all-connecting inventory
    # the leg is empty -> no gap, because emptiness is a hard-constraint problem.
    connecting = _flight("C1", "2026-09-01T09:00+08:00", "2026-09-01T18:00+09:00", 400, stops=1)
    request = _request(FlightPreferences(max_stops=0, avoid_red_eye=True))
    gaps = preference_gap_for_leg(
        [connecting], request, origin="SIN", dest="NRT",
        leg_date=__import__("datetime").date(2026, 9, 1), direction="OUTBOUND",
    )
    assert gaps == []


def test_relaxation_is_valid_only_for_a_real_gap():
    gaps = {"OUTBOUND": ["avoid_red_eye"], "RETURN": []}
    assert relaxation_is_valid(PreferenceRelaxation(field="avoid_red_eye", reason="x"), gaps) is True
    assert relaxation_is_valid(PreferenceRelaxation(field="prefer_direct", reason="x"), gaps) is False


def test_apply_relaxation_turns_off_exactly_one_soft_pref():
    prefs = FlightPreferences(avoid_red_eye=True, prefer_direct=True)
    relaxed = apply_relaxation(prefs, PreferenceRelaxation(field="avoid_red_eye", reason="x"))
    assert relaxed.avoid_red_eye is False
    assert relaxed.prefer_direct is True  # untouched


def test_apply_relaxation_removes_only_the_scoped_soft_arrival_pref():
    prefs = FlightPreferences(arrival_preferences=[
        ArrivalPreference(direction="OUTBOUND", by="2026-09-01T12:00+09:00", hard=False),
        ArrivalPreference(direction="RETURN", by="2026-09-05T20:00+08:00", hard=False),
    ])
    relaxed = apply_relaxation(prefs, PreferenceRelaxation(field="soft_arrival_preference", direction="OUTBOUND", reason="x"))
    remaining = {p.direction for p in relaxed.arrival_preferences}
    assert remaining == {"RETURN"}


# --- agent-level (LLM + code fence) ---

def test_valid_relaxation_triggers_second_tool_pass_and_is_recorded():
    request = _request(FlightPreferences(avoid_red_eye=True))
    first = FlightAgentResponse(
        rationale="Both options are red-eyes; proposing to relax that.",
        highlighted_flight_ids=["RE1", "RET1"],
        proposed_relaxation=PreferenceRelaxation(field="avoid_red_eye", reason="all outbound are red-eyes"),
        confidence=0.6,
    )
    second = FlightAgentResponse(
        rationale="After relaxing red-eye avoidance, RE1 is cheapest.",
        highlighted_flight_ids=["RE1", "RET1"],
        confidence=0.8,
    )
    llm, structured_llm = _fake_llm(first, second)

    proposal, response = run_flight_agent(request, INVENTORY, llm)

    assert structured_llm.invoke.call_count == 2  # reasoned again after relaxing
    assert response.relaxation_applied is not None
    assert response.relaxation_applied.field == "avoid_red_eye"
    assert response.rationale == second.rationale


def test_invalid_relaxation_is_ignored_not_applied():
    # prefer_direct isn't a real gap here (all flights are direct), so even if
    # the LLM proposes it, code must ignore it — no second pass, no application.
    request = _request(FlightPreferences(avoid_red_eye=True))
    only = FlightAgentResponse(
        rationale="Trying to relax the wrong thing.",
        highlighted_flight_ids=["RE1", "RET1"],
        proposed_relaxation=PreferenceRelaxation(field="prefer_direct", reason="not actually a gap"),
        confidence=0.5,
    )
    llm, structured_llm = _fake_llm(only)

    proposal, response = run_flight_agent(request, INVENTORY, llm)

    assert structured_llm.invoke.call_count == 1  # no second pass
    assert response.relaxation_applied is None


def test_no_relaxation_proposed_behaves_exactly_as_before():
    request = _request(FlightPreferences(avoid_red_eye=True))
    only = FlightAgentResponse(
        rationale="Presenting options as-is.",
        highlighted_flight_ids=["RE1", "RET1"],
        confidence=0.7,
    )
    llm, structured_llm = _fake_llm(only)

    proposal, response = run_flight_agent(request, INVENTORY, llm)

    assert structured_llm.invoke.call_count == 1
    assert response.relaxation_applied is None
    assert response is only


def test_relaxation_field_cannot_express_a_hard_constraint():
    # Schema-level fence: PreferenceRelaxation.field is a Literal, so a hard
    # constraint literally cannot be constructed as a relaxation target.
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        PreferenceRelaxation(field="max_price", reason="should be impossible")
