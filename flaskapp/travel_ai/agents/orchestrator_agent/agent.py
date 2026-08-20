"""Orchestrator Agent development and workflow-governance entry point.

This is the last place text can be stopped before a traveller reads it. The
specialists screen their own rationales (`flight_agent/reasoning.py:168`), but
their findings are then rewritten here into the prose that actually ships, and
that rewrite was previously unscreened — `assess_plan` runs afterwards and only
appends warnings, it never withholds. The L2 gate below closes that boundary.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from flaskapp.travel_ai.agents.base import compact
from flaskapp.travel_ai.agents.orchestrator_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.agents.shared import SYSTEM_POLICY
from flaskapp.travel_ai.guardrails.types import Decision
from flaskapp.travel_ai.schemas import TravelPlan
from flaskapp.travel_ai.terminal import log_payload
from flaskapp.travel_ai.usage import TokenUsageCallback
from flaskapp.database import save_agent_run

NAME = "orchestrator_agent"

MAX_ATTEMPTS = 2


def plan_text(plan: TravelPlan) -> str:
    """Every field of the plan a traveller actually reads.

    Structured numbers are excluded deliberately: they are already constrained
    by `TravelPlan` and checked by `assess_plan`, and feeding them to a
    classifier only adds tokens and false-positive surface.
    """
    return "\n".join([
        plan.title, plan.summary,
        *plan.itinerary, *plan.rationale, *plan.alternatives,
        *plan.assumptions, *plan.limitations,
    ])


def withheld_plan(findings) -> TravelPlan:
    """A safe plan for when generated prose fails the output gate twice.

    Raising instead would surface as a generic "Travel planning failed" and lose
    the specialists' work along with it. Withholding keeps the same contract the
    frontend already renders, states plainly that something was withheld, and
    preserves each specialist's own summary — those were screened by their own
    agents' output gates, so they are safe to show even when the synthesis of
    them was not.
    """
    return TravelPlan(
        title="Plan withheld by output guardrails",
        summary=(
            "A travel plan was generated but did not pass output screening, so it "
            "has been withheld. The specialist findings below were screened "
            "separately and are shown unchanged."
        ),
        itinerary=[],
        rationale=[f"{finding.agent}: {finding.summary}" for finding in findings],
        limitations=[
            "The synthesized plan was withheld by output guardrails.",
            "Human review is required before booking anything.",
        ],
    )


def create_node(llm, tracer, guardrail=None):
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
            # Retry once, then withhold — the same shape the specialists use for
            # a failed output gate (`flight_agent/reasoning.py:159-182`), rather
            # than a second failure path a reader would have to learn.
            plan = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                candidate = structured_llm.invoke(messages, config={"callbacks": [usage]})
                verdict = guardrail.screen_output(plan_text(candidate)) if guardrail else None
                if verdict is not None:
                    tracer.record("guardrail_llm_verdict", NAME, {
                        "gate": "output", "attempt": attempt, **verdict.as_audit_details(),
                    })
                if verdict is None or verdict.decision is not Decision.BLOCK:
                    plan = candidate
                    break
            if plan is None:
                plan = withheld_plan(findings)
                tracer.record("agent_output_withheld", NAME, {"attempts": MAX_ATTEMPTS})
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
