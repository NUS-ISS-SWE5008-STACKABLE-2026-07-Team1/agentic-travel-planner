"""LangGraph workflow composition."""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from flaskapp.config import Config
from flaskapp.travel_ai.a2a_client import create_remote_specialist_node
from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES, create_orchestrator_node
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
    full_a2a = bool(settings.get("A2A_INTERNAL_ENABLED", False))
    flight_a2a = (
        name == "flight_agent"
        and str(settings.get("FLIGHT_AGENT_TRANSPORT", "inprocess")).strip().lower()
        == "a2a"
    )
    if not (full_a2a or flight_a2a):
        return create_node(llm, tracer)
    endpoint = (
        settings.get("FLIGHT_AGENT_A2A_URL") if name == "flight_agent" else None
    ) or (
        f"{str(settings.get('A2A_BASE_URL', '')).rstrip('/')}/a2a/{name}"
    )
    return create_remote_specialist_node(
        name, tracer, endpoint=endpoint,
        timeout_seconds=float(settings.get(
            "FLIGHT_AGENT_A2A_TIMEOUT_SECONDS"
            if name == "flight_agent" else "AI_REQUEST_TIMEOUT_SECONDS",
            60,
        )),
    )


def build_travel_graph(llm: ChatOpenAI, tracer: AuditTracer, cancel_event=None,
                       guardrail=None, config=None, specialists=SPECIALISTS):
    """Compile a fan-out/fan-in graph: four specialists feed one orchestrator.

    `guardrail` is the L2 classifier, passed to the orchestrator so the final
    plan is screened before it reaches the traveller. None disables that gate;
    the deterministic checks in `assess_plan` run either way.

    `config` is the injection point for transport settings. Combined mode sets
    `A2A_INTERNAL_ENABLED`, routing all four specialists over official A2A;
    Flask-only mode retains direct in-process nodes.

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
