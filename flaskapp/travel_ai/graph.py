"""LangGraph workflow composition."""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES, create_orchestrator_node
from flaskapp.travel_ai.schemas import TravelGraphState
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.cancellation import PlanningCancelled

SPECIALISTS = tuple(SPECIALIST_NODE_FACTORIES)


def build_travel_graph(llm: ChatOpenAI, tracer: AuditTracer, cancel_event=None, guardrail=None):
    """Compile a fan-out/fan-in graph: four specialists feed one orchestrator.

    `guardrail` is the L2 classifier, passed to the orchestrator so the final
    plan is screened before it reaches the traveller. None disables that gate;
    the deterministic checks in `assess_plan` run either way.
    """
    # CUSTOMIZE THE LANGGRAPH WORKFLOW HERE.
    # Current design: START -> all four specialists in parallel -> orchestrator -> END.
    # Add conditional edges here if an agent should run only for certain requests.
    workflow = StateGraph(TravelGraphState)
    for name, create_node in SPECIALIST_NODE_FACTORIES.items():
        node = create_node(llm, tracer)
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
    workflow.add_edge(list(SPECIALISTS), "orchestrator_agent")
    workflow.add_edge("orchestrator_agent", END)
    return workflow.compile()
