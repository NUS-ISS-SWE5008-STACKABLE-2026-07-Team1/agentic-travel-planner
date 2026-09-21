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
    AGENT_SKILLS,
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


def test_orchestrator_agent_card_advertises_end_to_end_planning():
    card = build_agent_card("orchestrator_agent", "https://agents.example")
    body = MessageToDict(card, preserving_proto_field_name=True)

    assert body["supported_interfaces"][0]["url"] == (
        "https://agents.example/a2a/orchestrator_agent"
    )
    assert body["skills"][0]["id"] == "end-to-end-travel-planning"


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


def test_accessibility_executor_receives_upstream_candidate_findings():
    class RecordingQueue:
        def __init__(self):
            self.events = []

        async def enqueue_event(self, event):
            self.events.append(event)

    request_payload = {
        "origin": "Singapore", "destination": "Australia",
        "departure_date": "2026-10-10", "return_date": "2026-10-16",
        "travellers": 1, "traveller_ages": [30],
        "traveller_genders": ["prefer_not_to_say"],
        "traveller_accessibility_needs": [["step-free access"]], "budget": 3000,
    }
    candidate = AgentFinding(
        agent="hotel_transport_agent", summary="Hotel candidate", confidence=0.9,
    )
    message = new_data_message({
        "travel_request": request_payload,
        "candidate_findings": [candidate.model_dump(mode="json")],
    }, role=Role.ROLE_USER)
    context = RequestContext(
        ServerCallContext(), request=SendMessageRequest(message=message)
    )
    queue = RecordingQueue()
    executor = SpecialistAgentExecutor(
        "accessibility_agent",
        lambda _request_id: lambda state: {
            "findings": [AgentFinding(
                agent="accessibility_agent",
                summary=f"Audited {len(state['findings'])} candidate finding",
                confidence=0.9,
            )]
        },
    )

    asyncio.run(executor.execute(context, queue))

    artifact_event = next(
        event for event in queue.events if isinstance(event, TaskArtifactUpdateEvent)
    )
    finding = get_data_parts(artifact_event.artifact.parts)[0]
    assert finding["summary"] == "Audited 1 candidate finding"


def test_orchestrator_executor_returns_complete_plan_response_artifact():
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

    def plan(_request, request_id):
        return PlanResponse(
            request_id=request_id,
            plan=TravelPlan(
                title="A2A travel plan",
                summary="Coordinated specialist result",
                itinerary=["Fly to Japan"],
                rationale=["Validated by specialists"],
            ),
            agent_findings=[],
            trace_url=f"/api/v1/traces/{request_id}",
        )

    asyncio.run(OrchestratorAgentExecutor(plan).execute(context, queue))

    artifact_event = next(
        event for event in queue.events if isinstance(event, TaskArtifactUpdateEvent)
    )
    response = get_data_parts(artifact_event.artifact.parts)[0]
    assert artifact_event.artifact.name == "travel-plan-response"
    assert response["plan"]["title"] == "A2A travel plan"
    assert response["status"] == "completed"
    assert queue.events[-1].status.state == TaskState.TASK_STATE_COMPLETED


def _app(root_agent=None):
    return build_a2a_application(
        {"flight_agent": lambda _request_id: lambda _state: {
            "findings": [AgentFinding(agent="flight_agent", summary="No-op", confidence=1.0)]
        }},
        "https://agents.example",
        root_agent=root_agent,
    )


def test_a_card_is_served_at_the_origin_well_known_path():
    """Generic A2A tooling looks only here, so without it discovery fails.

    Per-agent paths are what let five agents share one origin, but the spec puts
    discovery at the origin root and the TCK cannot start a run without it.
    """
    response = TestClient(_app(root_agent="flight_agent")).get("/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.json()["name"] == "Flight Agent"


def test_root_agent_is_configurable_so_one_agent_can_be_certified_alone():
    body = TestClient(_app(root_agent="flight_agent")).get("/.well-known/agent-card.json").json()
    assert body["skills"][0]["id"] == "flight-planning"


def test_per_agent_card_still_served_alongside_the_root_card():
    client = TestClient(_app(root_agent="flight_agent"))
    assert client.get("/a2a/flight_agent/.well-known/agent-card.json").status_code == 200


def test_cards_do_not_advertise_streaming_we_do_not_implement():
    """The executors emit start -> artifact -> complete, with nothing in between."""
    for agent in AGENT_SKILLS:
        card = build_agent_card(agent, "https://agents.example")
        assert card.capabilities.streaming is False, agent


def test_every_agent_has_its_own_display_name():
    """Catches the `.replace(" planning", " Agent")` surgery that mislabelled four."""
    names = {
        agent: build_agent_card(agent, "https://agents.example").name
        for agent in AGENT_SKILLS
    }
    assert names["flight_agent"] == "Flight Agent"
    assert names["accessibility_agent"] == "Accessibility Agent"
    assert names["hotel_transport_agent"] == "Hotel and Ground Transport Agent"
    assert len(set(names.values())) == len(names), "display names must be distinct"


def test_jsonrpc_answers_on_both_slash_forms():
    """Found by the official TCK; no unit test would have produced this URL.

    A client handed our card's endpoint as an httpx `base_url` and posting to a
    relative "/" resolves to `/a2a/<agent>/`, with a trailing slash. That is
    what the TCK's JSON-RPC client does. Without a route for it the request
    fell past the A2A routes into the Flask catch-all and returned a 404 *HTML*
    page, so every generic client saw a JSON parse error instead of an agent —
    53 TCK failures from one missing slash.
    """
    client = TestClient(_app(root_agent="flight_agent"))
    body = {"jsonrpc": "2.0", "id": "1", "method": "GetTask",
            "params": {"id": "does-not-exist"}}
    headers = {"A2A-Version": "1.0"}

    for path in ("/a2a/flight_agent", "/a2a/flight_agent/"):
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("application/json"), path
        # -32001 TaskNotFound: a real protocol answer, not an HTML error page.
        assert response.json()["error"]["code"] == -32001, path


def test_agent_card_is_cacheable():
    """Clients poll the card for discovery; it changes only when we redeploy."""
    response = TestClient(_app(root_agent="flight_agent")).get(
        "/.well-known/agent-card.json"
    )

    assert "max-age" in response.headers.get("Cache-Control", "")
