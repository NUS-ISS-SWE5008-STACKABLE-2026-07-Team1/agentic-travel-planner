"""Accessibility Agent development and future bias-audit entry point."""

from flaskapp.travel_ai.agents.base import compact

from flaskapp.travel_ai.agents.accessibility_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.agents.accessibility_agent.retrieval import retrieve_accessibility_evidence
from flaskapp.travel_ai.agents.accessibility_agent.guardrails import (
    blocked_input_finding, enforce_accessibility_output, sanitize_evidence,
)
from flaskapp.travel_ai.agents.base import make_specialist_node

NAME = "accessibility_agent"


def _guarded_evidence(state):
    evidence = sanitize_evidence(retrieve_accessibility_evidence(state))
    candidates = []
    rejected = 0
    for finding in state.get("findings", []):
        if finding.agent == NAME:
            continue
        payload = finding.model_dump(mode="json")
        # A remote A2A caller can supply candidate artifacts. Treat them as
        # untrusted and refuse instruction-like or harmful peer output before
        # placing it in the model context.
        if blocked_input_finding({"request": {"preferences": [compact(payload)]}}):
            rejected += 1
            continue
        candidates.append(payload)
    evidence["candidate_findings"] = candidates
    evidence["rejected_candidate_findings"] = rejected
    return evidence


def create_node(llm, tracer):
    return make_specialist_node(
        NAME, INSTRUCTION, llm, tracer,
        context_provider=_guarded_evidence,
        preflight=blocked_input_finding,
        postprocess=enforce_accessibility_output,
    )
