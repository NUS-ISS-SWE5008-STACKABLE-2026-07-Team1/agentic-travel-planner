"""Call a specialist over the official A2A protocol instead of in-process.

This is the other half of `a2a_standard.py`. That module lets *other people's*
software call our agents; this one lets our own orchestrator do it, so the
flight agent really is talking to the orchestrator over Agent2Agent rather than
through a function call that happens to pass an envelope-shaped object.

**It is off by default.** `FLIGHT_AGENT_TRANSPORT=inprocess` keeps the direct
call, which is faster, has no second process to keep alive, and shares one
audit chain. Setting `a2a` routes the same node over HTTP. The switch follows
the `FLIGHT_AGENT_MODE` precedent exactly: resolved once when the graph is
built, never per request, so the transport cannot change under a traveller
mid-plan.

Three things learned from the SDK that are not obvious and are easy to get
wrong:

1. **`SendMessage` returns a task handle, not a result.** The server replies as
   soon as the task is created (`TASK_STATE_SUBMITTED`) and does the work
   afterwards, so the client polls `GetTask` until the task reaches a terminal
   state. That is the protocol's model, not a quirk of ours.
2. **The `A2A-Version` header decides which protocol version you are speaking.**
   Omit it and the server treats you as v0.3 and rejects you with `-32009`.
   The SDK client sets it; hand-rolled HTTP would not.
3. **v1.0 method names are PascalCase** (`SendMessage`, `GetTask`). The
   `message/send` spelling belongs to v0.3.

On failure this raises rather than falling back to the in-process node. A
silent fallback would make the switch untestable from the outside — you could
never tell whether the A2A path actually ran — and the barrier edge in
`graph.py` already means a flight failure fails the plan either way.
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from flaskapp.travel_ai.a2a import response_message
from flaskapp.travel_ai.schemas import AgentFinding, TravelRequest

logger = logging.getLogger(__name__)

# Metadata keys are namespaced. A bare `parent_request_id` risks colliding with
# another implementation's key and tells a reader of the wire format nothing
# about who owns it.
PARENT_REQUEST_ID_KEY = "travelplanner/parent_request_id"
CORRELATION_ID_KEY = "travelplanner/correlation_id"

EVENT_CALL_STARTED = "a2a_call_started"
EVENT_CALL_COMPLETED = "a2a_call_completed"
EVENT_CALL_FAILED = "a2a_transport_failed"

# Poll fast at first, then back off. A specialist takes 30-80 seconds, so a flat
# 50ms interval spent ~1,200 requests per agent waiting for one that was never
# going to arrive sooner: one real plan on GKE put ~5,100 JSON-RPC calls through
# the agents pod, and its own health probe timed out mid-plan. Starting at 50ms
# keeps a stubbed test (which completes immediately) as fast as it was, while
# the ceiling bounds a real wait to roughly 80 polls instead of 1,200. The cost
# is up to `_POLL_CEILING_SECONDS` of extra latency on the last poll.
_POLL_INTERVAL_SECONDS = 0.05
_POLL_BACKOFF = 1.5
_POLL_CEILING_SECONDS = 2.0


class RemoteAgentError(RuntimeError):
    """A specialist could not be reached, or did not return a usable finding.

    Distinct from the agent itself reporting a planning shortfall: "no flights
    for this route" is an `AgentFinding` the orchestrator can work with, while
    this means the conversation did not happen.
    """


def _terminal_states() -> tuple[int, ...]:
    from a2a.types import TaskState

    return (
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    )


def _run_blocking(coro, timeout: float):
    """Run a coroutine from synchronous code, whatever thread we are on.

    LangGraph's sync executor runs nodes on plain worker threads with no event
    loop, so `asyncio.run` is normally fine. But this node can also be reached
    from inside `SpecialistAgentExecutor`, which is already async — and calling
    `asyncio.run` on a thread that has a running loop raises. Detect that case
    and hand the work to a thread that does not.

    A fresh loop per call costs single-digit milliseconds against a flight
    agent that takes 5-8 seconds, so the simplicity is worth more than reuse.
    """
    async def _with_timeout():
        return await asyncio.wait_for(coro, timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_timeout())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_with_timeout())).result()


async def _request_finding(
    *,
    endpoint: str,
    agent_name: str,
    request: TravelRequest,
    parent_request_id: str,
    correlation_id: str,
    httpx_client_factory,
    deadline_seconds: float,
    candidate_findings: list[AgentFinding] | None = None,
) -> AgentFinding:
    import httpx
    from a2a.client import ClientConfig, create_client
    from a2a.helpers import get_data_parts, new_data_message
    from a2a.types import GetTaskRequest, Role, SendMessageRequest, TaskState

    # Built inside the coroutine deliberately: an AsyncClient created on one
    # event loop and closed on another raises, and this module makes a new loop
    # per call.
    client_context = (
        httpx_client_factory() if httpx_client_factory is not None
        else httpx.AsyncClient(base_url=endpoint, timeout=deadline_seconds)
    )
    async with client_context as http_client:
        config = ClientConfig(
            httpx_client=http_client,
            # Matches the Agent Card, which declares streaming false because
            # the executors emit no incremental updates.
            streaming=False,
            polling=True,
            supported_protocol_bindings=["JSONRPC"],
            accepted_output_modes=["application/json"],
        )
        client = await create_client(endpoint, config)

        payload = {"travel_request": request.model_dump(mode="json")}
        if candidate_findings:
            payload["candidate_findings"] = [
                item.model_dump(mode="json") for item in candidate_findings
            ]
        message = new_data_message(payload, role=Role.ROLE_USER)
        send_request = SendMessageRequest(message=message)
        # Request-level metadata, not the Message's own. `RequestContext.metadata`
        # — the accessor the server side uses — exposes `SendMessageRequest.metadata`,
        # so metadata attached to the message would arrive and be silently
        # unreadable. The parent's request id travels here so the specialist
        # writes into the same audit chain and planning job rather than opening
        # its own. See `a2a_standard._resolve_request_id`.
        send_request.metadata.update({
            PARENT_REQUEST_ID_KEY: parent_request_id,
            CORRELATION_ID_KEY: correlation_id,
        })

        task = None
        async for event in client.send_message(send_request):
            if event.HasField("task"):
                task = event.task
        if task is None:
            raise RemoteAgentError(f"{agent_name} returned no task for the request")

        terminal = _terminal_states()
        deadline = time.monotonic() + deadline_seconds
        interval = _POLL_INTERVAL_SECONDS
        while task.status.state not in terminal:
            if time.monotonic() > deadline:
                raise RemoteAgentError(
                    f"{agent_name} did not finish within {deadline_seconds:.0f}s"
                )
            await asyncio.sleep(interval)
            interval = min(interval * _POLL_BACKOFF, _POLL_CEILING_SECONDS)
            task = await client.get_task(GetTaskRequest(id=task.id))

        if task.status.state != TaskState.TASK_STATE_COMPLETED:
            raise RemoteAgentError(
                f"{agent_name} task ended as {TaskState.Name(task.status.state)}"
            )

        finding_artifact = next(
            (a for a in task.artifacts if a.name == "agent-finding"), None
        )
        if finding_artifact is None:
            raise RemoteAgentError(f"{agent_name} completed without an agent-finding artifact")
        parts = get_data_parts(finding_artifact.parts)
        if not parts:
            raise RemoteAgentError(f"{agent_name} returned an empty agent-finding artifact")
        return AgentFinding.model_validate(parts[0])


def create_remote_specialist_node(
    agent_name: str,
    tracer,
    *,
    endpoint: str,
    timeout_seconds: float = 60.0,
    httpx_client_factory=None,
):
    """A graph node that calls `agent_name` over A2A instead of running it here.

    Returns the same shape the in-process node returns —
    `{"findings": [...], "messages": [...]}` — so `merge_findings`,
    `merge_messages` and `save_plan` cannot tell the two apart. The outgoing
    envelope is rebuilt on this side with `response_message` rather than being
    shipped over the wire, because that envelope is an in-process contract and
    putting it on the wire would make it part of the endpoint's public shape.
    """

    def remote_node(state: dict[str, Any]) -> dict[str, Any]:
        incoming = next(
            (m for m in state.get("messages", [])
             if m.message_type == "request" and m.recipient == agent_name),
            None,
        )
        if incoming is None:
            raise ValueError(f"Missing A2A request for {agent_name}")
        request = TravelRequest.model_validate(state["request"])
        request_id = str(state["request_id"])

        tracer.record(EVENT_CALL_STARTED, agent_name, {"endpoint": endpoint})
        started = time.monotonic()
        try:
            finding = _run_blocking(
                _request_finding(
                    endpoint=endpoint,
                    agent_name=agent_name,
                    request=request,
                    parent_request_id=request_id,
                    correlation_id=str(incoming.correlation_id),
                    httpx_client_factory=httpx_client_factory,
                    deadline_seconds=timeout_seconds,
                    candidate_findings=(
                        list(state.get("findings", []))
                        if agent_name == "accessibility_agent" else None
                    ),
                ),
                timeout_seconds + 5,
            )
        except Exception as exc:
            # Codes and counts, never free text from a remote system.
            tracer.record(EVENT_CALL_FAILED, agent_name, {
                "error_type": type(exc).__name__,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            })
            logger.exception("A2A call to %s failed", agent_name)
            raise RemoteAgentError(f"{agent_name} could not be reached over A2A") from exc

        tracer.record(EVENT_CALL_COMPLETED, agent_name, {
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        })
        outgoing = response_message(
            request=incoming, sender=agent_name,
            payload_type="AgentFinding", payload=finding,
        )
        return {"findings": [finding], "messages": [outgoing]}

    return remote_node
