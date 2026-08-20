"""Unverified accessibility is a third state, not a synonym for unavailable.

Seed rows always state a real True/False. A live supplier feed (Duffel) has no
such field at all, so `None` arrives the moment inventory goes live. These
tests pin the three behaviours that follow from treating it as its own case:

- it does not silently exclude the traveller from every live flight,
- it does not silently pass as "checked",
- and an explicit orchestrator constraint still refuses to accept it.

Collapsing `None` into either boolean breaks exactly one of those, and the
person who finds out is a wheelchair user at the gate.
"""

from __future__ import annotations

from flaskapp.travel_ai.agents.flight_agent.agent import (
    UNVERIFIED_STEP_FREE,
    UNVERIFIED_WHEELCHAIR,
    _accessibility_limitations,
)
from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights, screen_flights
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightCandidate,
    FlightConstraints,
    FlightInventoryItem,
    FlightProposalRequest,
    TripContext,
)


def _flight(flight_id: str, *, assist, price=300.0, direction="OUT") -> FlightInventoryItem:
    outbound = direction == "OUT"
    return FlightInventoryItem(
        flight_id=flight_id,
        carrier="ZZ",
        flight_no=flight_id,
        origin_airport="LHR" if outbound else "JFK",
        dest_airport="JFK" if outbound else "LHR",
        dep_ts="2026-09-01T08:00:00+01:00" if outbound else "2026-09-05T18:00:00-04:00",
        arr_ts="2026-09-01T11:10:00-04:00" if outbound else "2026-09-06T06:00:00+01:00",
        duration_min=430,
        price=price,
        cabin_class="ECONOMY",
        seats_available=4,
        stops=0,
        wheelchair_assist_available=assist,
        step_free_boarding=None,
        source="duffel",
    )


def _request(*, needs_wheelchair=True, constraints=None) -> FlightProposalRequest:
    return FlightProposalRequest(
        trip_context=TripContext(
            origin_country="United Kingdom",
            dest_country="United States",
            origin_airport="LHR",
            dest_airport="JFK",
            depart_date="2026-09-01",
            return_date="2026-09-05",
            traveller_ages=[34],
            accessibility_needs=["wheelchair assistance"] if needs_wheelchair else [],
            currency="GBP",
        ),
        constraints=constraints,
    )


RETURN_LEG = _flight("RET1", assist=None, direction="RET")


def test_unknown_assistance_does_not_exclude_a_wheelchair_user():
    """Excluding on unknown would return zero options to every wheelchair user
    the moment inventory comes from a live feed — which reads as "no flights
    exist for you" rather than the truth, "we could not check"."""
    proposal = propose_flights(_request(), [_flight("UNKNOWN1", assist=None), RETURN_LEG])
    assert [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"] == ["UNKNOWN1"]


def test_a_flight_known_not_to_offer_assistance_is_still_excluded():
    """The existing hard filter is unchanged — only `None` behaves differently."""
    proposal = propose_flights(_request(), [_flight("NOASSIST", assist=False), RETURN_LEG])
    assert not [c for c in proposal.candidates if c.direction == "OUTBOUND"]


def test_unknown_carries_through_to_the_candidate_rather_than_being_normalised():
    proposal = propose_flights(_request(), [_flight("UNKNOWN1", assist=None), RETURN_LEG])
    outbound = next(c for c in proposal.candidates if c.direction == "OUTBOUND")
    assert outbound.wheelchair_assist_available is None
    assert outbound.source == "duffel"


def test_verified_assistance_outranks_unverified_even_when_dearer():
    """For the traveller this applies to, a confirmed accommodation beats a
    cheaper flight whose accommodation is merely unknown."""
    cheap_unknown = _flight("CHEAP", assist=None, price=100.0)
    dear_verified = _flight("VERIFIED", assist=True, price=900.0)
    proposal = propose_flights(_request(), [cheap_unknown, dear_verified, RETURN_LEG])
    outbound = [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]
    assert outbound == ["VERIFIED", "CHEAP"]


def test_that_ranking_preference_is_inert_for_travellers_who_did_not_ask():
    """No accessibility need stated means price ordering is untouched — which
    is also why this change cannot move any existing seed-based ranking."""
    cheap_unknown = _flight("CHEAP", assist=None, price=100.0)
    dear_verified = _flight("VERIFIED", assist=True, price=900.0)
    proposal = propose_flights(
        _request(needs_wheelchair=False), [cheap_unknown, dear_verified, RETURN_LEG]
    )
    outbound = [c.flight_id for c in proposal.candidates if c.direction == "OUTBOUND"]
    assert outbound == ["CHEAP", "VERIFIED"]


def test_an_explicit_require_wheelchair_assist_constraint_rejects_unknown():
    """Fail-closed here, unlike the implicit filter. This constraint is
    orchestrator-issued mid-negotiation — typically after Accessibility Agent
    vetoed a round — so "the supplier doesn't publish it" is not good enough."""
    request = _request(constraints=FlightConstraints(require_wheelchair_assist=True))
    proposal = propose_flights(request, [_flight("UNKNOWN1", assist=None), RETURN_LEG])
    assert not [c for c in proposal.candidates if c.direction == "OUTBOUND"]


def test_the_screening_trace_distinguishes_unverified_from_not_offered():
    """Explainability: a rejection reason has to say which of the two it was."""
    request = _request(constraints=FlightConstraints(require_wheelchair_assist=True))
    results = {
        s.flight_id: s
        for s in screen_flights(request, [_flight("UNKNOWN1", assist=None), _flight("NOASSIST", assist=False)])
    }
    assert "cannot be verified" in " ".join(results["UNKNOWN1"].reasons)
    assert "not met" in " ".join(results["NOASSIST"].reasons)


def _candidate(assist, step_free) -> FlightCandidate:
    return FlightCandidate(
        flight_id="X",
        direction="OUTBOUND",
        dep_ts="2026-09-01T08:00:00+01:00",
        arr_ts="2026-09-01T11:10:00-04:00",
        dest_airport="JFK",
        stops=0,
        price=300.0,
        seats=4,
        wheelchair_assist_available=assist,
        step_free_boarding=step_free,
    )


def test_the_traveller_facing_option_says_unverified_not_unavailable():
    limitations = _accessibility_limitations(_candidate(None, None))
    assert UNVERIFIED_WHEELCHAIR in limitations
    assert UNVERIFIED_STEP_FREE in limitations
    assert not any("is not offered" in text for text in limitations)


def test_a_flight_that_genuinely_lacks_assistance_still_says_so():
    limitations = _accessibility_limitations(_candidate(False, False))
    assert limitations == ["Wheelchair assistance is not offered on this flight."]


def test_a_fully_verified_flight_carries_no_accessibility_limitation():
    assert _accessibility_limitations(_candidate(True, True)) == []
