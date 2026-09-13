from starlette.testclient import TestClient

from flaskapp.combined import create_application


def test_combined_application_serves_web_and_agent_cards():
    node_factories = {
        "accessibility_agent": lambda _request_id: lambda _state: {"findings": []}
    }
    application = create_application(
        base_url="http://127.0.0.1:5000",
        node_factories=node_factories,
        orchestrator_runner=lambda _request, _request_id: None,
    )
    client = TestClient(application)

    web_response = client.get("/")
    card_response = client.get(
        "/a2a/accessibility_agent/.well-known/agent-card.json"
    )
    orchestrator_card = client.get(
        "/a2a/orchestrator_agent/.well-known/agent-card.json"
    )

    assert web_response.status_code == 200
    assert "Sign in" in web_response.text
    assert card_response.status_code == 200
    assert card_response.json()["supportedInterfaces"][0]["url"] == (
        "http://127.0.0.1:5000/a2a/accessibility_agent"
    )
    assert orchestrator_card.status_code == 200
    assert orchestrator_card.json()["skills"][0]["id"] == (
        "end-to-end-travel-planning"
    )
