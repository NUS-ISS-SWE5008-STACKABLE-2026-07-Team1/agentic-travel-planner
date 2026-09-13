import httpx

from flaskapp.travel_ai.a2a_client import A2ASpecialistClient
from flaskapp.travel_ai.a2a_standard import build_a2a_application
from flaskapp.travel_ai.schemas import AgentFinding, TravelRequest


def test_specialist_client_uses_agent_card_jsonrpc_and_artifact():
    app = build_a2a_application(
        {"flight_agent": lambda _request_id: lambda state: {
            "findings": [AgentFinding(
                agent="flight_agent",
                summary=f"Reviewed {state['request']['destination']}",
                confidence=0.9,
            )]
        }},
        "http://testserver",
    )
    client = A2ASpecialistClient(
        "http://testserver",
        transport=httpx.ASGITransport(app=app),
    )
    request = TravelRequest.model_validate({
        "origin": "Singapore",
        "destination": "Japan",
        "departure_date": "2026-10-10",
        "return_date": "2026-10-16",
        "travellers": 1,
        "traveller_ages": [30],
        "traveller_genders": ["prefer_not_to_say"],
        "traveller_accessibility_needs": [[]],
        "budget": 3000,
    })

    finding = client.invoke(
        "flight_agent", request, "135f773e-c7b8-4a37-a681-b5ea0fb57ee7"
    )

    assert finding.agent == "flight_agent"
    assert finding.summary == "Reviewed Japan"
