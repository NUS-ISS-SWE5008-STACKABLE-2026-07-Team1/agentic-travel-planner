import asyncio

from a2a.helpers import get_data_parts, new_data_message
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import (
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict
from starlette.testclient import TestClient

from flaskapp.travel_ai.a2a_standard import (
    OrchestratorAgentExecutor,
    SpecialistAgentExecutor,
    build_a2a_application,
    build_agent_card,
)
from flaskapp.travel_ai.schemas import AgentFinding, PlanResponse, TravelPlan


def test_agent_card_is_official_a2a_1_contract():
    card = build_agent_card("accessibility_agent", "https://agents.example")
    body = MessageToDict(card, preserving_proto_field_name=True)

    assert body["supported_interfaces"] == [{
        "url": "https://agents.example/a2a/accessibility_agent",
        "protocol_binding": "JSONRPC",
        "protocol_version": "1.0",
    }]
    assert body["skills"][0]["id"] == "travel-accessibility-review"
    assert body["default_input_modes"] == ["application/json"]


def test_orchestrator_agent_card_is_discoverable():
    card = build_agent_card("orchestrator_agent", "https://agents.example")
    body = MessageToDict(card, preserving_proto_field_name=True)

    assert body["skills"][0]["id"] == "end-to-end-travel-planning"
    assert body["supported_interfaces"][0]["url"].endswith(
        "/a2a/orchestrator_agent"
    )


def test_agent_card_endpoint_is_discoverable():
    app = build_a2a_application(
        {"flight_agent": lambda _request_id: lambda _state: {
            "findings": [AgentFinding(
                agent="flight_agent", summary="No-op", confidence=1.0
            )]
        }},
        "https://agents.example",
    )
    client = TestClient(app)

    response = client.get("/a2a/flight_agent/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.json()["name"] == "Flight Agent"
    assert response.json()["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"


def test_unknown_specialist_card_is_rejected():
    try:
        build_agent_card("unknown_agent", "https://agents.example")
    except ValueError as exc:
        assert "Unknown A2A agent" in str(exc)
    else:
        raise AssertionError("unknown specialist should fail")


def test_executor_returns_finding_as_a2a_artifact():
    class RecordingQueue:
        def __init__(self):
            self.events = []

        async def enqueue_event(self, event):
            self.events.append(event)

    request_payload = {
        "origin": "Singapore",
        "destination": "Japan",
        "departure_date": "2026-10-10",
        "return_date": "2026-10-16",
        "travellers": 1,
        "traveller_ages": [30],
        "traveller_genders": ["prefer_not_to_say"],
        "traveller_accessibility_needs": [[]],
        "budget": 3000,
    }
    message = new_data_message(request_payload, role=Role.ROLE_USER)
    context = RequestContext(
        ServerCallContext(), request=SendMessageRequest(message=message)
    )
    queue = RecordingQueue()
    executor = SpecialistAgentExecutor(
        "flight_agent",
        lambda _request_id: lambda state: {
            "findings": [AgentFinding(
                agent="flight_agent",
                summary=f"Reviewed {state['request']['destination']}",
                confidence=0.9,
            )]
        },
    )

    asyncio.run(executor.execute(context, queue))

    assert isinstance(queue.events[0], Task)
    artifact_event = next(
        event for event in queue.events if isinstance(event, TaskArtifactUpdateEvent)
    )
    finding = get_data_parts(artifact_event.artifact.parts)[0]
    assert finding["agent"] == "flight_agent"
    assert finding["summary"] == "Reviewed Japan"
    final_event = queue.events[-1]
    assert isinstance(final_event, TaskStatusUpdateEvent)
    assert final_event.status.state == TaskState.TASK_STATE_COMPLETED


def test_orchestrator_executor_returns_plan_response_artifact():
    class RecordingQueue:
        def __init__(self):
            self.events = []

        async def enqueue_event(self, event):
            self.events.append(event)

    request_payload = {
        "origin": "Singapore", "destination": "Japan",
        "departure_date": "2026-10-10", "return_date": "2026-10-16",
        "travellers": 1, "traveller_ages": [30],
        "traveller_genders": ["prefer_not_to_say"],
        "traveller_accessibility_needs": [[]], "budget": 3000,
    }
    message = new_data_message(request_payload, role=Role.ROLE_USER)
    context = RequestContext(
        ServerCallContext(), request=SendMessageRequest(message=message)
    )
    queue = RecordingQueue()

    def run_plan(_request, request_id):
        return PlanResponse(
            request_id=request_id,
            plan=TravelPlan(
                title="Japan trip", summary="Complete plan", itinerary=["Day 1"],
                rationale=["Matches request"],
            ),
            agent_findings=[], trace_url=f"/traces/{request_id}",
        )

    asyncio.run(OrchestratorAgentExecutor(run_plan).execute(context, queue))

    artifact_event = next(
        event for event in queue.events if isinstance(event, TaskArtifactUpdateEvent)
    )
    response = get_data_parts(artifact_event.artifact.parts)[0]
    assert artifact_event.artifact.name == "travel-plan-response"
    assert response["plan"]["title"] == "Japan trip"
    assert queue.events[-1].status.state == TaskState.TASK_STATE_COMPLETED
