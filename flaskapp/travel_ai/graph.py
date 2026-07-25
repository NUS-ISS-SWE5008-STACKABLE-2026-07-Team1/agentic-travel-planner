"""LangGraph workflow composition."""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES, create_orchestrator_node
from flaskapp.travel_ai.schemas import TravelGraphState
from flaskapp.travel_ai.tracing import AuditTracer

SPECIALISTS = tuple(SPECIALIST_NODE_FACTORIES)


def build_travel_graph(llm: ChatOpenAI, tracer: AuditTracer):
    """Compile a fan-out/fan-in graph: four specialists feed one orchestrator."""
    # CUSTOMIZE THE LANGGRAPH WORKFLOW HERE.
    # Current design: START -> all four specialists in parallel -> orchestrator -> END.
    # Add conditional edges here if an agent should run only for certain requests.
    workflow = StateGraph(TravelGraphState)
    for name, create_node in SPECIALIST_NODE_FACTORIES.items():
        workflow.add_node(name, create_node(llm, tracer))
        workflow.add_edge(START, name)
    workflow.add_node("orchestrator_agent", create_orchestrator_node(llm, tracer))
    # A list-valued source is a barrier: synthesis starts only after every branch.
    workflow.add_edge(list(SPECIALISTS), "orchestrator_agent")
    workflow.add_edge("orchestrator_agent", END)
    return workflow.compile()
