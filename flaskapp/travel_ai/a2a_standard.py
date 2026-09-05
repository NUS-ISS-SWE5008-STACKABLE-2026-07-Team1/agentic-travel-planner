"""Official A2A 1.x adapters for the existing specialist agents.

The application historically used :mod:`flaskapp.travel_ai.a2a`, a useful
in-process envelope that is not the Agent2Agent wire protocol.  This module is
the interoperability boundary: it publishes Agent Cards, accepts official A2A
tasks, and converts JSON data Parts to the project's validated domain models.
Agent reasoning remains transport-independent and is reused unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID, uuid4

from a2a.helpers import (
    get_data_parts,
    new_data_part,
    new_task_from_user_message,
    new_text_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette

from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.schemas import AgentFinding, PlanResponse, TravelRequest

A2A_PROTOCOL_VERSION = "1.0"
JSON_MEDIA_TYPE = "application/json"

AGENT_SKILLS: dict[str, dict[str, Any]] = {
    "orchestrator_agent": {
        "id": "end-to-end-travel-planning",
        "name": "Travel Planning Orchestrator",
        "description": (
            "Coordinate specialist agents and produce an assured end-to-end travel plan."
        ),
        "tags": ["travel", "orchestration", "multi-agent", "planning"],
    },
    "flight_agent": {
        "id": "flight-planning",
        "name": "Flight planning",
        "description": "Find and assess flight options for a validated travel request.",
        "tags": ["travel", "flights", "airports"],
    },
    "hotel_transport_agent": {
        "id": "hotel-ground-transport-planning",
        "name": "Hotel and ground transport planning",
        "description": "Find compatible accommodation and local transport options.",
        "tags": ["travel", "hotel", "transport"],
    },
    "accessibility_agent": {
        "id": "travel-accessibility-review",
        "name": "Travel accessibility review",
        "description": "Assess travel options against stated accessibility requirements.",
        "tags": ["travel", "accessibility", "evidence"],
    },
    "risk_advisory_agent": {
        "id": "travel-risk-advisory",
        "name": "Travel risk advisory",
        "description": "Identify destination and itinerary risks and mitigations.",
        "tags": ["travel", "risk", "advisory"],
    },
}

SPECIALIST_AGENT_NAMES = frozenset(AGENT_SKILLS) - {"orchestrator_agent"}


def build_agent_card(agent_name: str, base_url: str) -> AgentCard:
    """Return an official, discoverable Agent Card for one agent."""
    try:
        skill = AGENT_SKILLS[agent_name]
    except KeyError as exc:
        raise ValueError(f"Unknown A2A agent: {agent_name}") from exc
    display_name = skill["name"].replace(" planning", " Agent")
    endpoint = f"{base_url.rstrip('/')}/a2a/{agent_name}"
    return AgentCard(
        name=display_name,
        description=skill["description"],
        version="1.0.0",
        supported_interfaces=[AgentInterface(
            url=endpoint,
            protocol_binding="JSONRPC",
            protocol_version=A2A_PROTOCOL_VERSION,
        )],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=[JSON_MEDIA_TYPE],
        default_output_modes=[JSON_MEDIA_TYPE],
        skills=[AgentSkill(
            id=skill["id"],
            name=skill["name"],
            description=skill["description"],
            tags=skill["tags"],
            input_modes=[JSON_MEDIA_TYPE],
            output_modes=[JSON_MEDIA_TYPE],
        )],
    )


def _travel_request_from_context(context: RequestContext) -> TravelRequest:
    if context.message is None:
        raise ValueError("A2A request must contain a message")
    data_parts = get_data_parts(context.message.parts)
    if len(data_parts) != 1 or not isinstance(data_parts[0], dict):
        raise ValueError("A2A request must contain exactly one JSON data Part")
    payload = data_parts[0]
    # Accept a bare request as the canonical shape. The named wrapper is useful
    # to clients that include additional message metadata.
    request_data = payload.get("travel_request", payload)
    return TravelRequest.model_validate(request_data)


class SpecialistAgentExecutor(AgentExecutor):
    """Bridge an existing synchronous specialist node to an A2A task."""

    def __init__(
        self,
        agent_name: str,
        node_factory: Callable[[str], Callable[[dict[str, Any]], dict[str, Any]]],
    ) -> None:
        if agent_name not in SPECIALIST_AGENT_NAMES:
            raise ValueError(f"Unknown A2A specialist: {agent_name}")
        self.agent_name = agent_name
        self.node_factory = node_factory

    @staticmethod
    def _correlation_id(context_id: str) -> UUID:
        try:
            return UUID(context_id)
        except (TypeError, ValueError, AttributeError):
            return uuid4()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ValueError("A2A request must contain a message")
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        try:
            request = _travel_request_from_context(context)
            legacy_request = request_message(
                correlation_id=self._correlation_id(task.context_id),
                sender="orchestrator_agent",
                recipient=self.agent_name,
                payload_type="TravelRequest",
                payload=request,
            )
            state = {
                "request_id": task.id,
                "request": request.model_dump(mode="json"),
                "findings": [],
                "messages": [legacy_request],
            }
            node = self.node_factory(task.id)
            result = await asyncio.to_thread(node, state)
            findings = result.get("findings", [])
            if len(findings) != 1:
                raise ValueError("Specialist must return exactly one AgentFinding")
            finding = AgentFinding.model_validate(findings[0])
            await updater.add_artifact(
                [new_data_part(finding.model_dump(mode="json"), JSON_MEDIA_TYPE)],
                name="agent-finding",
                metadata={"agent": self.agent_name, "schema": "AgentFinding"},
                last_chunk=True,
            )
            await updater.complete(new_text_message(
                f"{self.agent_name} completed the request.",
                context_id=task.context_id,
                task_id=task.id,
            ))
        except Exception as exc:
            await updater.failed(new_text_message(
                f"{self.agent_name} could not complete the request ({type(exc).__name__}).",
                context_id=task.context_id,
                task_id=task.id,
            ))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            raise ValueError("Cannot cancel an A2A request without a task")
        await TaskUpdater(event_queue, task.id, task.context_id).cancel()


class OrchestratorAgentExecutor(AgentExecutor):
    """Expose end-to-end travel planning as an official A2A task."""

    def __init__(
        self,
        planning_runner: Callable[[TravelRequest, str], PlanResponse],
    ) -> None:
        self.planning_runner = planning_runner

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ValueError("A2A request must contain a message")
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        try:
            request = _travel_request_from_context(context)
            # TravelPlanningService uses a UUID request id. A2A task/context ids
            # are opaque strings, so normalize the context id at this boundary.
            request_id = str(SpecialistAgentExecutor._correlation_id(task.context_id))
            response = await asyncio.to_thread(
                self.planning_runner, request, request_id
            )
            response = PlanResponse.model_validate(response)
            await updater.add_artifact(
                [new_data_part(response.model_dump(mode="json"), JSON_MEDIA_TYPE)],
                name="travel-plan-response",
                metadata={"agent": "orchestrator_agent", "schema": "PlanResponse"},
                last_chunk=True,
            )
            await updater.complete(new_text_message(
                "orchestrator_agent completed the travel plan.",
                context_id=task.context_id,
                task_id=task.id,
            ))
        except Exception as exc:
            await updater.failed(new_text_message(
                "orchestrator_agent could not complete the request "
                f"({type(exc).__name__}).",
                context_id=task.context_id,
                task_id=task.id,
            ))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            raise ValueError("Cannot cancel an A2A request without a task")
        await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _add_agent_routes(routes, agent_name, executor, base_url) -> None:
    card = build_agent_card(agent_name, base_url)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    endpoint = f"/a2a/{agent_name}"
    routes.extend(create_jsonrpc_routes(handler, rpc_url=endpoint))
    routes.extend(create_agent_card_routes(
        card,
        card_url=f"{endpoint}/.well-known/agent-card.json",
    ))


def build_a2a_application(
    node_factories: Mapping[
        str, Callable[[str], Callable[[dict[str, Any]], dict[str, Any]]]
    ],
    base_url: str,
    orchestrator_runner: Callable[[TravelRequest, str], PlanResponse] | None = None,
) -> Starlette:
    """Build one ASGI application hosting specialist and orchestrator endpoints."""
    routes = []
    for agent_name, node_factory in node_factories.items():
        _add_agent_routes(
            routes,
            agent_name,
            SpecialistAgentExecutor(agent_name, node_factory),
            base_url,
        )
    if orchestrator_runner is not None:
        _add_agent_routes(
            routes,
            "orchestrator_agent",
            OrchestratorAgentExecutor(orchestrator_runner),
            base_url,
        )
    return Starlette(routes=routes)
