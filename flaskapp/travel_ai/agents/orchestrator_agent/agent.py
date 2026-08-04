"""Orchestrator Agent development and workflow-governance entry point."""

from langchain_core.messages import HumanMessage, SystemMessage

from flaskapp.travel_ai.agents.base import compact
from flaskapp.travel_ai.agents.orchestrator_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.agents.shared import SYSTEM_POLICY
from flaskapp.travel_ai.schemas import TravelPlan
from flaskapp.travel_ai.terminal import log_payload
from flaskapp.travel_ai.usage import TokenUsageCallback
from flaskapp.database import save_agent_run

NAME = "orchestrator_agent"


def create_node(llm, tracer):
    structured_llm = llm.with_structured_output(TravelPlan, method="json_schema")

    def orchestrate(state):
        findings = state.get("findings", [])
        tracer.record("agent_started", NAME, {"finding_count": len(findings)})
        if tracer.database_path:
            save_agent_run(tracer.database_path, state["request_id"], NAME, "processing")
        messages = [
            SystemMessage(content=SYSTEM_POLICY + "\n" + INSTRUCTION),
            HumanMessage(content=compact({
                "request": state["request"],
                "specialist_findings": [item.model_dump() for item in findings],
            })),
        ]
        usage = TokenUsageCallback()
        try:
            plan = structured_llm.invoke(messages, config={"callbacks": [usage]})
            log_payload(
                f"REQUEST {state['request_id']} | ORCHESTRATOR_AGENT RESPONSE", plan
            )
            tracer.record("agent_completed", NAME, {
                "itinerary_steps": len(plan.itinerary),
                "source_count": len(plan.sources),
                **usage.as_dict(),
            })
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path, state["request_id"], NAME, "completed",
                    usage.as_dict(), plan,
                )
            return {"plan": plan}
        except Exception as exc:
            tracer.record("agent_failed", NAME, {"error_type": type(exc).__name__})
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path, state["request_id"], NAME, "failed",
                    usage.as_dict(), error_type=type(exc).__name__,
                )
            raise

    return orchestrate
