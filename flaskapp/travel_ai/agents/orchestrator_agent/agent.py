"""Orchestrator Agent development and workflow-governance entry point."""

from langchain_core.messages import HumanMessage, SystemMessage

from flaskapp.travel_ai.agents.base import compact
from flaskapp.travel_ai.agents.orchestrator_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.agents.shared import SYSTEM_POLICY
from flaskapp.travel_ai.schemas import TravelPlan

NAME = "orchestrator_agent"


def create_node(llm, tracer):
    structured_llm = llm.with_structured_output(TravelPlan, method="json_schema")

    def orchestrate(state):
        findings = state.get("findings", [])
        tracer.record("agent_started", NAME, {"finding_count": len(findings)})
        messages = [
            SystemMessage(content=SYSTEM_POLICY + "\n" + INSTRUCTION),
            HumanMessage(content=compact({
                "request": state["request"],
                "specialist_findings": [item.model_dump() for item in findings],
            })),
        ]
        try:
            plan = structured_llm.invoke(messages)
            tracer.record("agent_completed", NAME, {
                "itinerary_steps": len(plan.itinerary),
                "source_count": len(plan.sources),
            })
            return {"plan": plan}
        except Exception as exc:
            tracer.record("agent_failed", NAME, {"error_type": type(exc).__name__})
            raise

    return orchestrate
