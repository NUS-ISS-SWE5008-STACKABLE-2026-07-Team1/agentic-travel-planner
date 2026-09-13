"""Official A2A client used by the orchestrator to invoke specialists."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest, TaskState

from flaskapp.travel_ai.cancellation import PlanningCancelled
from flaskapp.travel_ai.schemas import AgentFinding, TravelRequest


class A2AAgentError(RuntimeError):
    """A remote A2A agent failed or returned an invalid artifact."""


class A2ASpecialistClient:
    """Discover and invoke specialist agents through official A2A JSON-RPC."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 180,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def invoke(
        self,
        agent_name: str,
        request: TravelRequest,
        context_id: str,
        cancel_event=None,
    ) -> AgentFinding:
        """Synchronous LangGraph seam around the SDK's asynchronous client."""
        return asyncio.run(
            self._invoke(agent_name, request, context_id, cancel_event)
        )

    async def _invoke(
        self,
        agent_name: str,
        request: TravelRequest,
        context_id: str,
        cancel_event=None,
    ) -> AgentFinding:
        endpoint = f"{self.base_url}/a2a/{agent_name}"
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
        ) as http_client:
            factory = ClientFactory(ClientConfig(
                streaming=True,
                polling=False,
                httpx_client=http_client,
                supported_protocol_bindings=["JSONRPC"],
                accepted_output_modes=["application/json"],
            ))
            client = await factory.create_from_url(endpoint)
            message = new_data_message(
                request.model_dump(mode="json"),
                media_type="application/json",
                context_id=context_id,
                role=Role.ROLE_USER,
            )
            finding = None
            failed_states = {
                TaskState.TASK_STATE_FAILED,
                TaskState.TASK_STATE_CANCELED,
                TaskState.TASK_STATE_REJECTED,
                TaskState.TASK_STATE_AUTH_REQUIRED,
                TaskState.TASK_STATE_INPUT_REQUIRED,
            }
            async for event in client.send_message(
                SendMessageRequest(message=message)
            ):
                if cancel_event is not None and cancel_event.is_set():
                    raise PlanningCancelled("Planning was cancelled")
                payload_type = event.WhichOneof("payload")
                if payload_type == "task":
                    for artifact in event.task.artifacts:
                        finding = self._finding_from_artifact(artifact, finding)
                    if event.task.status.state in failed_states:
                        raise A2AAgentError(
                            f"{agent_name} ended in state {event.task.status.state}"
                        )
                elif payload_type == "artifact_update":
                    finding = self._finding_from_artifact(
                        event.artifact_update.artifact, finding
                    )
                elif (
                    payload_type == "status_update"
                    and event.status_update.status.state in failed_states
                ):
                    raise A2AAgentError(
                        f"{agent_name} ended in state "
                        f"{event.status_update.status.state}"
                    )
            if finding is None:
                raise A2AAgentError(
                    f"{agent_name} completed without an AgentFinding artifact"
                )
            if finding.agent != agent_name:
                raise A2AAgentError(
                    f"Expected {agent_name} artifact, received {finding.agent}"
                )
            return finding

    @staticmethod
    def _finding_from_artifact(artifact, current):
        if artifact.name != "agent-finding":
            return current
        parts = get_data_parts(artifact.parts)
        if len(parts) != 1 or not isinstance(parts[0], dict):
            raise A2AAgentError("agent-finding must contain one JSON data Part")
        return AgentFinding.model_validate(parts[0])
