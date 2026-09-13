"""LangGraph workflow composition."""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from flaskapp.config import Config
from flaskapp.travel_ai.a2a_client import create_remote_specialist_node
from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES, create_orchestrator_node
from flaskapp.travel_ai.agents.flight_agent.agent import NAME as FLIGHT_NAME
from flaskapp.travel_ai.schemas import TravelGraphState
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.cancellation import PlanningCancelled

SPECIALISTS = tuple(SPECIALIST_NODE_FACTORIES)


def _specialist_node(name, create_node, llm, tracer, settings):
    """The in-process node, or a remote one speaking A2A to the same agent.

    The choice is made HERE, when the graph is built, and never inside the node.
    Two reasons, and the first is not obvious: the A2A *server* builds its
    specialist nodes from this same registry, so a branch inside `create_node`
    would have the served flight agent call itself over HTTP. The second is the
    `FLIGHT_AGENT_MODE` precedent — transport is a composition concern, and
    resolving it once means it cannot change under a traveller mid-plan.
    """
    if name != FLIGHT_NAME:
        return create_node(llm, tracer)
    transport = str(settings.get("FLIGHT_AGENT_TRANSPORT", "inprocess")).strip().lower()
    if transport != "a2a":
        return create_node(llm, tracer)
    endpoint = settings.get("FLIGHT_AGENT_A2A_URL") or (
        f"{str(settings.get('A2A_BASE_URL', '')).rstrip('/')}/a2a/{FLIGHT_NAME}"
    )
    return create_remote_specialist_node(
        FLIGHT_NAME, tracer, endpoint=endpoint,
        timeout_seconds=float(settings.get("FLIGHT_AGENT_A2A_TIMEOUT_SECONDS", 60)),
    )


def build_travel_graph(llm: ChatOpenAI, tracer: AuditTracer, cancel_event=None,
                       guardrail=None, config=None, specialists=SPECIALISTS):
    """Compile a fan-out/fan-in graph: four specialists feed one orchestrator.

    `guardrail` is the L2 classifier, passed to the orchestrator so the final
    plan is screened before it reaches the traveller. None disables that gate;
    the deterministic checks in `assess_plan` run either way.

    `config` is the injection point for settings, mirroring
    `flight_agent.create_node(llm, tracer, provider=None, config=None)`. It
    decides how the flight agent is reached — see `_specialist_node`.

    `specialists` is which of them to build. Nodes, START edges and the barrier
    all read this one value, which is what keeps the join correct: the barrier
    joins on a fixed source list, so a graph that still contained a node nobody
    routed to would never fire it. Defaults to all four, so every existing
    caller is unaffected. `dispatch.specialists_for` produces it, and the caller
    must pass the SAME value to the A2A message list — `make_specialist_node`
    raises `Missing A2A request` for a node that runs without one.
    """
    # CUSTOMIZE THE LANGGRAPH WORKFLOW HERE.
    # START -> the selected specialists in parallel -> orchestrator -> END.
    settings = vars(Config) if config is None else config
    workflow = StateGraph(TravelGraphState)
    selected = tuple(name for name in SPECIALISTS if name in set(specialists))
    for name in selected:
        node = _specialist_node(name, SPECIALIST_NODE_FACTORIES[name], llm, tracer, settings)
        def cancellable_specialist(state, node=node):
            if cancel_event and cancel_event.is_set():
                raise PlanningCancelled("Planning was cancelled")
            result = node(state)
            if cancel_event and cancel_event.is_set():
                raise PlanningCancelled("Planning was cancelled")
            return result
        workflow.add_node(name, cancellable_specialist)
        workflow.add_edge(START, name)
    orchestrator = create_orchestrator_node(llm, tracer, guardrail)
    def cancellable_orchestrator(state):
        if cancel_event and cancel_event.is_set():
            raise PlanningCancelled("Planning was cancelled")
        result = orchestrator(state)
        if cancel_event and cancel_event.is_set():
            raise PlanningCancelled("Planning was cancelled")
        return result
    workflow.add_node("orchestrator_agent", cancellable_orchestrator)
    # A list-valued source is a barrier: synthesis starts only after every branch.
    workflow.add_edge(list(selected), "orchestrator_agent")
    workflow.add_edge("orchestrator_agent", END)
    return workflow.compile()
