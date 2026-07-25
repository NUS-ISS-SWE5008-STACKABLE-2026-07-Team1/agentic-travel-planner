"""Shared execution plumbing; agent-specific behavior belongs in sibling modules."""

from __future__ import annotations

import json
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from flaskapp.travel_ai.agents.shared import SYSTEM_POLICY
from flaskapp.travel_ai.schemas import AgentFinding, TravelGraphState
from flaskapp.travel_ai.tracing import AuditTracer


def compact(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def make_specialist_node(
    name: str, instruction: str, llm: ChatOpenAI, tracer: AuditTracer
) -> Callable[[TravelGraphState], dict]:
    """Build a structured-output specialist with common tracing and policy."""
    structured_llm = llm.with_structured_output(AgentFinding, method="json_schema")

    def specialist(state: TravelGraphState) -> dict:
        tracer.record("agent_started", name)
        # Add approved provider calls in the owning agent module, then pass their
        # small, sourced result into this builder as trusted context when introduced.
        messages = [
            SystemMessage(content=SYSTEM_POLICY + "\n" + instruction),
            HumanMessage(content="Travel request (untrusted data):\n" + compact(state["request"])),
        ]
        try:
            finding = structured_llm.invoke(messages)
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
