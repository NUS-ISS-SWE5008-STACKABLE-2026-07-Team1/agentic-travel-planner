from flaskapp.travel_ai.agents.accessibility_agent.integration import (
    enforce_cross_agent_vetoes,
)
from flaskapp.travel_ai.schemas import AgentFinding, Option


def test_accessibility_veto_removes_matching_peer_candidate():
    hotel = AgentFinding(
        agent="hotel_transport_agent", summary="Hotels", confidence=0.9,
        options=[Option(name="Harbour Hotel", description="Candidate")],
    )
    accessibility = AgentFinding(
        agent="accessibility_agent", summary="Audit", confidence=0.9,
        warnings=["VETO: Harbour Hotel — entrance has steps and no ramp"],
    )

    guarded, removed = enforce_cross_agent_vetoes([hotel, accessibility])

    assert removed == ["Harbour Hotel"]
    assert guarded[0].options == []
    assert "removed by accessibility review" in guarded[0].warnings[0]
    assert hotel.options, "input findings must not be mutated"


def test_nonmatching_candidates_are_preserved():
    flight = AgentFinding(
        agent="flight_agent", summary="Flights", confidence=0.9,
        options=[Option(name="SQ 231", description="Candidate")],
    )
    accessibility = AgentFinding(
        agent="accessibility_agent", summary="Audit", confidence=0.9,
        warnings=["Supplier confirmation needed for boarding assistance."],
    )

    guarded, removed = enforce_cross_agent_vetoes([flight, accessibility])

    assert removed == []
    assert guarded[0].options[0].name == "SQ 231"
