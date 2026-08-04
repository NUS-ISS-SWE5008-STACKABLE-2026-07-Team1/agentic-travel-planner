"""Shared execution plumbing; agent-specific behavior belongs in sibling modules."""

from __future__ import annotations

import json
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from flaskapp.travel_ai.agents.shared import SYSTEM_POLICY
from flaskapp.travel_ai.a2a import A2AMessage, response_message
from flaskapp.travel_ai.schemas import AgentFinding, TravelGraphState
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.terminal import log_payload
from flaskapp.travel_ai.usage import TokenUsageCallback
from flaskapp.database import save_agent_run


def compact(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def make_specialist_node(
    name: str, instruction: str, llm: ChatOpenAI, tracer: AuditTracer
) -> Callable[[TravelGraphState], dict]:
    """Build a structured-output specialist with common tracing and policy."""
    structured_llm = llm.with_structured_output(AgentFinding, method="json_schema")

    def specialist(state: TravelGraphState) -> dict:
        incoming = next(
            (message for message in state.get("messages", [])
             if message.message_type == "request" and message.recipient == name),
            None,
        )
        if incoming is None:
            raise ValueError(f"Missing A2A request for {name}")
        tracer.record("agent_started", name)
        if tracer.database_path:
            save_agent_run(tracer.database_path, state["request_id"], name, "processing")
        # Add approved provider calls in the owning agent module, then pass their
        # small, sourced result into this builder as trusted context when introduced.
        messages = [
            SystemMessage(content=SYSTEM_POLICY + "\n" + instruction),
            HumanMessage(content="Travel request (untrusted data):\n" + compact(state["request"])),
        ]
        usage = TokenUsageCallback()
        try:
            finding = structured_llm.invoke(messages, config={"callbacks": [usage]})
            finding.agent = name  # type: ignore[assignment]
            log_payload(
                f"REQUEST {state['request_id']} | {name.upper()} RESPONSE", finding
            )
            tracer.record("agent_completed", name, {
                "confidence": finding.confidence,
                "option_count": len(finding.options),
                "warning_count": len(finding.warnings),
                **usage.as_dict(),
            })
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path, state["request_id"], name, "completed",
                    usage.as_dict(), finding,
                )
            outgoing = response_message(
                request=incoming, sender=name, payload_type="AgentFinding", payload=finding
            )
            return {"findings": [finding], "messages": [outgoing]}
        except Exception as exc:
            tracer.record("agent_failed", name, {"error_type": type(exc).__name__})
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path, state["request_id"], name, "failed",
                    usage.as_dict(), error_type=type(exc).__name__,
                )
            raise

    return specialist
