"""Implementations of the five requested agents."""

from __future__ import annotations

import json
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from flaskapp.travel_ai.prompts import ORCHESTRATOR_INSTRUCTION, SPECIALIST_INSTRUCTIONS, SYSTEM_POLICY
from flaskapp.travel_ai.schemas import AgentFinding, TravelGraphState, TravelPlan
from flaskapp.travel_ai.tracing import AuditTracer


def _compact(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def make_specialist_node(
    name: str, llm: ChatOpenAI, tracer: AuditTracer
) -> Callable[[TravelGraphState], dict]:
    """Create a structured-output specialist node with consistent tracing."""
    structured_llm = llm.with_structured_output(AgentFinding, method="json_schema")

    def specialist(state: TravelGraphState) -> dict:
        tracer.record("agent_started", name)

        # CUSTOMIZE AGENT EXECUTION HERE:
        # 1. Call an approved flight/hotel/maps/advisory API before building messages.
        # 2. Convert the API response to a small, trusted context object.
        # 3. Add that context to the HumanMessage below with clear source URLs.
        #
        # Example (after implementing the function in tools.py):
        # live_context = search_travel_provider(name, state["request"])
        # HumanMessage(content=_compact({
        #     "request": state["request"],
        #     "verified_provider_data": live_context,
        # }))
        #
        # Keep API keys in .env/config.py. Never place secrets in a prompt or trace.
        messages = [
            SystemMessage(content=SYSTEM_POLICY + "\n" + SPECIALIST_INSTRUCTIONS[name]),
            HumanMessage(content="Travel request (untrusted data):\n" + _compact(state["request"])),
        ]
        try:
            finding = structured_llm.invoke(messages)
            # Enforce identity even if a model returns the wrong label.
            finding.agent = name  # type: ignore[assignment]
            tracer.record("agent_completed", name, {
                "confidence": finding.confidence,
                "option_count": len(finding.options),
                "warning_count": len(finding.warnings),
            })
            return {"findings": [finding]}
        except Exception as exc:
            tracer.record("agent_failed", name, {"error_type": type(exc).__name__})
            raise

    return specialist


def make_orchestrator_node(llm: ChatOpenAI, tracer: AuditTracer) -> Callable[[TravelGraphState], dict]:
    """Create the orchestrator that integrates, rather than searches for, options."""
    structured_llm = llm.with_structured_output(TravelPlan, method="json_schema")

    def orchestrate(state: TravelGraphState) -> dict:
        name = "orchestrator_agent"
        tracer.record("agent_started", name, {"finding_count": len(state.get("findings", []))})
        # CUSTOMIZE ORCHESTRATOR INPUT HERE if it needs extra verified context.
        # Usually the orchestrator should synthesize specialist outputs rather than
        # independently search, which keeps decisions easier to trace and explain.
        messages = [
            SystemMessage(content=SYSTEM_POLICY + "\n" + ORCHESTRATOR_INSTRUCTION),
            HumanMessage(content=_compact({
                "request": state["request"],
                "specialist_findings": [item.model_dump() for item in state.get("findings", [])],
            })),
        ]
        try:
            plan = structured_llm.invoke(messages)
            tracer.record("agent_completed", name, {
                "itinerary_steps": len(plan.itinerary),
                "source_count": len(plan.sources),
            })
            return {"plan": plan}
        except Exception as exc:
            tracer.record("agent_failed", name, {"error_type": type(exc).__name__})
            raise

    return orchestrate
