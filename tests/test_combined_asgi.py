from starlette.testclient import TestClient

from flaskapp.combined import create_application
from flaskapp.travel_ai.schemas import AgentFinding


def test_combined_application_serves_web_and_agent_cards():
    def accessibility_node_factory(_request_id):
        return lambda _state: {
            "findings": [AgentFinding(
                agent="accessibility_agent",
                summary="Credential-free CI test finding",
                confidence=1.0,
            )]
        }

    application = create_application(
        base_url="http://127.0.0.1:5000",
        node_factories={"accessibility_agent": accessibility_node_factory},
    )
    client = TestClient(application)

    web_response = client.get("/")
    card_response = client.get(
        "/a2a/accessibility_agent/.well-known/agent-card.json"
    )

    assert web_response.status_code == 200
    assert "Sign in" in web_response.text
    assert card_response.status_code == 200
    assert card_response.json()["supportedInterfaces"][0]["url"] == (
        "http://127.0.0.1:5000/a2a/accessibility_agent"
    )
