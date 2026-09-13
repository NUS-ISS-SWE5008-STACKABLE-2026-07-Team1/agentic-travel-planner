"""Official A2A 1.x adapters for the existing specialist agents.

The application historically used :mod:`flaskapp.travel_ai.a2a`, a useful
in-process envelope that is not the Agent2Agent wire protocol.  This module is
the interoperability boundary: it publishes Agent Cards, accepts official A2A
tasks, and converts JSON data Parts to the project's validated domain models.
Agent reasoning remains transport-independent and is reused unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, PROTOCOL_VERSION_1_0
from a2a.utils.errors import (
    A2AError, ContentTypeNotSupportedError, InvalidAgentResponseError,
    InvalidParamsError, InvalidRequestError,
)
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

from flaskapp.database import ensure_planning_job, update_planning_job
from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.a2a_client import PARENT_REQUEST_ID_KEY
from flaskapp.travel_ai.guardrails import LlmGuardrail
from flaskapp.travel_ai.safeguards import (
    GuardrailBlocked, SafetyError, screen_request_l2, validate_request,
)
from flaskapp.travel_ai.schemas import AgentFinding, PlanResponse, TravelRequest

logger = logging.getLogger(__name__)

# Falls back to the same default `Config.MAX_INPUT_CHARS` carries, so an
# executor built without one is bounded rather than unbounded.
DEFAULT_MAX_INPUT_CHARS = 20_000


@dataclass(frozen=True)
class ExecutorContext:
    """What an executor needs from the application hosting it.

    One object rather than a widening list of constructor arguments: every
    field here is something both executors need, and the set grows as the A2A
    surface catches up with the HTTP one.

    Every field is optional so a test can drive an executor with an injected
    node and no application at all — but note what each omission costs.
    `database_path=None` skips run bookkeeping; `guardrail_settings=None` skips
    the L2 classifier exactly as `screen_request_l2(request, None)` does on the
    website path.
    """

    database_path: Any = None
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    guardrail_settings: Mapping[str, Any] | None = None
    # Whether to believe a caller that names the request id it wants its work
    # recorded under. True only when the endpoint is not open to strangers:
    # an id is a write key into someone else's audit trail and planning job.
    trust_caller_request_id: bool = False

    def guardrail(self) -> Any:
        """The L2 classifier, or None when it is not configured."""
        if not self.guardrail_settings:
            return None
        return LlmGuardrail.from_settings(self.guardrail_settings)

    @classmethod
    def from_flask_config(cls, config: Mapping[str, Any]) -> "ExecutorContext":
        from flaskapp.travel_ai.guardrails import guardrail_settings

        return cls(
            database_path=config.get("DATABASE"),
            max_input_chars=config.get("MAX_INPUT_CHARS", DEFAULT_MAX_INPUT_CHARS),
            guardrail_settings=guardrail_settings(config),
            trust_caller_request_id=bool(config.get("A2A_TRUST_CALLER_REQUEST_ID", False)),
        )


A2A_PROTOCOL_VERSION = PROTOCOL_VERSION_1_0
# Bumped when this adapter's behaviour changes, independently of the protocol.
AGENT_IMPLEMENTATION_VERSION = "1.1.0"
JSON_MEDIA_TYPE = "application/json"

AGENT_SKILLS: dict[str, dict[str, Any]] = {
    "orchestrator_agent": {
        "id": "end-to-end-travel-planning",
        "display_name": "Travel Planning Orchestrator",
        "name": "Travel Planning Orchestrator",
        "description": (
            "Coordinate specialist agents and produce an assured end-to-end travel plan."
        ),
        "tags": ["travel", "orchestration", "multi-agent", "planning"],
    },
    "flight_agent": {
        "id": "flight-planning",
        "display_name": "Flight Agent",
        "name": "Flight planning",
        "description": "Find and assess flight options for a validated travel request.",
        "tags": ["travel", "flights", "airports"],
    },
    "hotel_transport_agent": {
        "id": "hotel-ground-transport-planning",
        "display_name": "Hotel and Ground Transport Agent",
        "name": "Hotel and ground transport planning",
        "description": "Find compatible accommodation and local transport options.",
        "tags": ["travel", "hotel", "transport"],
    },
    "accessibility_agent": {
        "id": "travel-accessibility-review",
        "display_name": "Accessibility Agent",
        "name": "Travel accessibility review",
        "description": "Assess travel options against stated accessibility requirements.",
        "tags": ["travel", "accessibility", "evidence"],
    },
    "risk_advisory_agent": {
        "id": "travel-risk-advisory",
        "display_name": "Risk and Advisory Agent",
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
    # An explicit name per agent. This was `skill["name"].replace(" planning",
    # " Agent")`, which happened to read correctly for the flight agent and
    # mislabelled the other four ("Travel accessibility review" has no
    # substring to replace, so it kept a skill name as its agent name).
    display_name = skill["display_name"]
    endpoint = f"{base_url.rstrip('/')}/a2a/{agent_name}"
    return AgentCard(
        name=display_name,
        description=skill["description"],
        # The agent implementation's version, which is not the protocol's.
        # `AgentInterface.protocol_version` below carries that one.
        version=AGENT_IMPLEMENTATION_VERSION,
        supported_interfaces=[AgentInterface(
            url=endpoint,
            protocol_binding="JSONRPC",
            protocol_version=A2A_PROTOCOL_VERSION,
        )],
        # Truthfully false. The executors emit start -> one artifact -> complete
        # with no incremental updates, and nothing in this system consumes
        # progress events: the browser progress bar is driven by trace polling,
        # and the orchestrator waits on a barrier edge for all four specialists
        # regardless. Declaring a capability we do not implement would also opt
        # us into the TCK's streaming tests for no benefit.
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
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


def _payload_from_context(context: RequestContext) -> dict[str, Any]:
    """The traveller's request as a plain dict, or an A2A error saying why not.

    Raises the protocol's own error types rather than `ValueError`, because
    these are all *caller* mistakes and the caller can only act on them if they
    arrive as distinguishable codes. `ContentTypeNotSupportedError` (-32005)
    specifically tells a client that sent prose that this agent wants a JSON
    data part, which "invalid params" would not.
    """
    if context.message is None:
        raise InvalidRequestError("An A2A request must contain a message")
    data_parts = get_data_parts(context.message.parts)
    if not data_parts:
        raise ContentTypeNotSupportedError(
            "This agent accepts one JSON data part containing a travel request; "
            "text and file parts are not supported."
        )
    if len(data_parts) != 1 or not isinstance(data_parts[0], dict):
        raise InvalidParamsError("Exactly one JSON data part is required")
    payload = data_parts[0]
    # Accept a bare request as the canonical shape. The named wrapper is useful
    # to clients that include additional message metadata.
    return payload.get("travel_request", payload)


def _screen_request(context: RequestContext, executor_context: ExecutorContext) -> TravelRequest:
    """The same gate `api.py:create_travel_plan` puts in front of the website.

    Deliberately the same order and the same helpers, not a parallel
    implementation: L0 size and sensitive-key rejection plus the L1 injection
    sweep in `validate_request`, then the L2 classifier in `screen_request_l2`.
    An A2A caller is not more trusted than a browser, and until this existed it
    was considerably *less* screened.

    Every failure becomes an A2A error rather than a failed task — see
    `SpecialistAgentExecutor.execute` for why that distinction matters.
    """
    payload = _payload_from_context(context)
    try:
        request = validate_request(payload, executor_context.max_input_chars)
        screen_request_l2(request, executor_context.guardrail())
    except GuardrailBlocked as exc:
        # Mirrors `api.py`: the verdict goes to the log, never to the caller.
        # A classifier that names the rule it tripped is a free oracle for
        # tuning an attack against it (`safeguards.py` states this at length),
        # and `str(exc)` is already the deliberately vague sentence.
        logger.warning("A2A L2 guardrail blocked a request: %s", exc.verdict.as_audit_details())
        raise InvalidParamsError(str(exc)) from exc
    except SafetyError as exc:
        raise InvalidParamsError(str(exc)) from exc
    except ValidationError as exc:
        raise InvalidParamsError(
            f"Travel request failed validation ({exc.error_count()} problem(s))"
        ) from exc
    return request


class SpecialistAgentExecutor(AgentExecutor):
    """Bridge an existing synchronous specialist node to an A2A task."""

    def __init__(
        self,
        agent_name: str,
        node_factory: Callable[[str], Callable[[dict[str, Any]], dict[str, Any]]],
        context: ExecutorContext | None = None,
    ) -> None:
        if agent_name not in SPECIALIST_AGENT_NAMES:
            raise ValueError(f"Unknown A2A specialist: {agent_name}")
        self.agent_name = agent_name
        self.node_factory = node_factory
        self.context = context or ExecutorContext()

    @property
    def database_path(self) -> Any:
        return self.context.database_path

    @staticmethod
    def _correlation_id(context_id: str) -> UUID:
        try:
            return UUID(context_id)
        except (TypeError, ValueError, AttributeError):
            return uuid4()

    def _resolve_request_id(self, context: RequestContext, task_id: str) -> tuple[str, bool]:
        """The id this work is recorded under, and whether we inherited it.

        The orchestrator sends its own request id as message metadata so the
        specialist's trace events join the parent's hash chain instead of
        starting a second one. Honoured only when the deployment says callers
        can be trusted — otherwise anyone who can reach the endpoint could name
        another traveller's request id and write into their audit trail.

        Falls back to the A2A task id, which the SDK generates as a UUID, so
        the trace filename stays valid for `/api/v1/traces/<uuid>`.
        """
        if not self.context.trust_caller_request_id:
            return task_id, False
        metadata = getattr(context, "metadata", None) or {}
        claimed = metadata.get(PARENT_REQUEST_ID_KEY)
        if not claimed:
            return task_id, False
        try:
            return str(UUID(str(claimed))), True
        except (TypeError, ValueError):
            logger.warning("Ignoring a malformed parent request id from an A2A caller")
            return task_id, False

    def _ensure_job(self, request_id: str, request: TravelRequest) -> None:
        if self.database_path is None:
            return
        ensure_planning_job(
            self.database_path, request_id, None, request.model_dump(mode="json")
        )

    def _finish_job(self, request_id: str, status: str, error_type: str | None = None) -> None:
        """Leave the job in a terminal state so `/admin` does not show it queued.

        Never raises: a bookkeeping failure must not turn a completed piece of
        work into a failed task. The task's own state is the contract with the
        caller; this table is our own telemetry.
        """
        if self.database_path is None:
            return
        try:
            update_planning_job(self.database_path, request_id, status, error_type)
        except Exception:  # noqa: BLE001 - telemetry must not mask the result
            logger.warning("Could not update planning job %s to %s", request_id, status)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Screening happens BEFORE a task exists, and that ordering is the point.
        #
        # Previously the task was created and `start_work()` fired first, so a
        # malformed or blocked request could only ever be reported as a *failed
        # task* — indistinguishable, to a client, from "the agent crashed".
        # Raising an A2AError here instead produces a real JSON-RPC error code
        # (-32602 invalid params, -32005 wrong content type), which is the
        # difference between a caller being able to fix their request and being
        # left guessing. Nothing is enqueued, so no phantom task is left behind
        # for a request that never began.
        request = _screen_request(context, self.context)

        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        request_id, inherited = self._resolve_request_id(context, task.id)

        try:
            legacy_request = request_message(
                correlation_id=self._correlation_id(task.context_id),
                sender="orchestrator_agent",
                recipient=self.agent_name,
                payload_type="TravelRequest",
                payload=request,
            )
            state = {
                "request_id": request_id,
                "request": request.model_dump(mode="json"),
                "findings": [],
                "messages": [legacy_request],
            }
            # Before the node runs, not after: the node's own first act is to
            # write an `agent_runs` row, and that column references
            # `planning_jobs`. No row here means a foreign-key failure that
            # surfaces as an opaque failed task with nothing naming the cause.
            #
            # `user_id=None` is correct rather than a gap — an A2A caller is a
            # peer agent with no browser session, and `owns_request` treats an
            # unowned job as readable by nobody, which is what we want.
            self._ensure_job(request_id, request)
            node = self.node_factory(request_id)
            result = await asyncio.to_thread(node, state)
            findings = result.get("findings", [])
            if len(findings) != 1:
                # -32006 exists for exactly this: our own agent misbehaved, and
                # that is not the caller's fault nor a generic crash.
                raise InvalidAgentResponseError(
                    f"{self.agent_name} must return exactly one AgentFinding"
                )
            finding = AgentFinding.model_validate(findings[0])
            await updater.add_artifact(
                [new_data_part(finding.model_dump(mode="json"), JSON_MEDIA_TYPE)],
                name="agent-finding",
                metadata={"agent": self.agent_name, "schema": "AgentFinding"},
                last_chunk=True,
            )
            # An inherited job belongs to the caller's plan; `jobs.py` owns its
            # lifecycle and this specialist must not declare it finished.
            if not inherited:
                self._finish_job(request_id, "completed")
            await updater.complete(new_text_message(
                f"{self.agent_name} completed the request.",
                context_id=task.context_id,
                task_id=task.id,
            ))
        except A2AError as exc:
            # A protocol-level fault raised after the task began — currently only
            # `InvalidAgentResponseError`, meaning our own agent returned
            # something unusable. The task is marked failed so it is not left
            # dangling in `working`, and the error is re-raised so the caller
            # receives its specific code rather than a generic failure. Losing
            # that distinction is what the blanket handler below used to do to
            # every error in this method.
            if not inherited:
                self._finish_job(request_id, "failed", type(exc).__name__)
            await updater.failed(new_text_message(
                f"{self.agent_name} returned an unusable response.",
                context_id=task.context_id,
                task_id=task.id,
            ))
            raise
        except Exception as exc:
            # A genuine failure during the work. The exception type goes to the
            # log and the tracer, never into the caller-facing message — an
            # internal class name tells an outside caller nothing actionable and
            # discloses our internals.
            logger.exception("%s failed while executing an A2A task", self.agent_name)
            if not inherited:
                self._finish_job(request_id, "failed", type(exc).__name__)
            await updater.failed(new_text_message(
                f"{self.agent_name} could not complete the request.",
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
        context: ExecutorContext | None = None,
    ) -> None:
        self.planning_runner = planning_runner
        self.context = context or ExecutorContext()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Screened before the task exists, for the same reason as the specialist
        # executor. Note this runs the gate twice for the default runner in
        # `scripts.a2a_server`, which screens again inside itself — harmless,
        # deterministic, and the alternative is an executor that trusts its
        # caller to have screened, which is how gaps like this one appear.
        request = _screen_request(context, self.context)
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        try:
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


# An Agent Card changes only when the deployment does, so it is exactly the
# kind of resource HTTP caching exists for. Clients are expected to poll it for
# discovery, and without these headers every poll refetches the whole document.
AGENT_CARD_MAX_AGE_SECONDS = 300


class _AgentCardCacheHeaders(BaseHTTPMiddleware):
    """Add `Cache-Control` and `ETag` to Agent Card responses.

    A weak ETag derived from the body, rather than a version string: the card
    is assembled from config at startup, so two deployments of the same code
    can legitimately serve different cards and must not share a validator.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if not request.url.path.endswith("agent-card.json"):
            return response
        response.headers.setdefault(
            "Cache-Control", f"public, max-age={AGENT_CARD_MAX_AGE_SECONDS}"
        )
        body = getattr(response, "body", None)
        if body is not None and "ETag" not in response.headers:
            response.headers["ETag"] = f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'
        return response


# Exported so a host application composing these routes into its own Starlette
# app carries the middleware with them. `combined.py` builds a new Starlette
# from `a2a_app.routes`, which silently drops anything attached to the inner
# app — the Agent Card cache headers vanished exactly that way.
A2A_MIDDLEWARE: tuple = (Middleware(_AgentCardCacheHeaders),)


def _add_agent_routes(routes, agent_name, executor, base_url, task_store, at_root=False) -> None:
    card = build_agent_card(agent_name, base_url)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=card,
    )
    endpoint = f"/a2a/{agent_name}"
    routes.extend(create_jsonrpc_routes(handler, rpc_url=endpoint))
    # The same handler again at the trailing-slash form, and this is not
    # cosmetic. A client given our card's URL as an httpx `base_url` and posting
    # to a relative "/" — which is what the official TCK's JSON-RPC client does,
    # and what the SDK's own conventions produce — resolves to
    # `/a2a/<agent>/`. Without this route that request falls past the A2A
    # routes into the Flask catch-all mount and comes back as a 404 *HTML*
    # page, so the caller sees a JSON parse error rather than an agent. Found
    # by running the TCK; no unit test would have produced that URL.
    routes.extend(create_jsonrpc_routes(handler, rpc_url=f"{endpoint}/"))
    routes.extend(create_agent_card_routes(
        card,
        card_url=f"{endpoint}/.well-known/agent-card.json",
    ))
    if at_root:
        # A second copy of this agent's card at the origin's well-known path.
        #
        # Per-agent paths are what let five agents share one origin, but the
        # spec puts discovery at `/.well-known/agent-card.json` and generic
        # tooling - the TCK and a2a-inspector among them - looks only there.
        # Without this a conformance run cannot even begin. Which agent answers
        # at the root is `A2A_ROOT_AGENT`, so the flight agent can be put
        # forward for certification without disturbing the default front door.
        routes.extend(create_agent_card_routes(
            card, card_url=AGENT_CARD_WELL_KNOWN_PATH
        ))


def build_a2a_application(
    node_factories: Mapping[
        str, Callable[[str], Callable[[dict[str, Any]], dict[str, Any]]]
    ],
    base_url: str,
    orchestrator_runner: Callable[[TravelRequest, str], PlanResponse] | None = None,
    context: ExecutorContext | None = None,
    root_agent: str | None = None,
) -> Starlette:
    """Build one ASGI application hosting specialist and orchestrator endpoints.

    `context` carries the database path, input limit and guardrail settings.
    Omitted, the executors skip run bookkeeping and the L2 classifier, which is
    right for a test driving an injected node and wrong for production — build
    it with `ExecutorContext.from_flask_config(app.config)` there.
    """
    context = context or ExecutorContext()
    root_agent = root_agent or "orchestrator_agent"
    # One store shared by every handler rather than one each. Task ids are
    # globally unique, so sharing is safe, and five independent unbounded
    # dictionaries is five leaks instead of one bounded cache.
    task_store = InMemoryTaskStore()
    routes = []
    for agent_name, node_factory in node_factories.items():
        _add_agent_routes(
            routes,
            agent_name,
            SpecialistAgentExecutor(agent_name, node_factory, context),
            base_url,
            task_store,
            at_root=(agent_name == root_agent),
        )
    if orchestrator_runner is not None:
        _add_agent_routes(
            routes,
            "orchestrator_agent",
            OrchestratorAgentExecutor(orchestrator_runner, context),
            base_url,
            task_store,
            at_root=(root_agent == "orchestrator_agent"),
        )
    return Starlette(routes=routes, middleware=list(A2A_MIDDLEWARE))
