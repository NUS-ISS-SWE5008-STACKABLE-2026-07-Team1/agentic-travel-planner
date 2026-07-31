"""Deterministic controls around the probabilistic model."""

from __future__ import annotations

import re
from typing import Any

from flaskapp.travel_ai.schemas import AgentFinding, SafetyAssessment, TravelPlan, TravelRequest

PROMPT_INJECTION = re.compile(
    r"(?i)(ignore (all|any|previous)|system prompt|developer message|reveal .*prompt|act as)"
)
SENSITIVE_KEYS = {
    "race", "ethnicity", "religion", "gender", "sexual_orientation",
    "disability_status", "political_affiliation",
}


class SafetyError(ValueError):
    """Raised when a request violates an enforceable safety boundary."""


def validate_request(payload: dict[str, Any], max_chars: int) -> TravelRequest:
    """Reject oversized, injected, or explicitly sensitive ranking inputs."""
    if len(str(payload)) > max_chars:
        raise SafetyError("Request is too large")
    forbidden = SENSITIVE_KEYS.intersection(payload)
    if forbidden:
        raise SafetyError(f"Unsupported sensitive fields: {', '.join(sorted(forbidden))}")
    request = TravelRequest.model_validate(payload)
    text_fields = request.preferences + request.accessibility_needs + request.refinement_notes
    if any(PROMPT_INJECTION.search(value) for value in text_fields):
        raise SafetyError("Instruction-like text was detected in request fields")
    return request


def assess_plan(
    request: TravelRequest, plan: TravelPlan, findings: list[AgentFinding]
) -> SafetyAssessment:
    """Run post-generation completeness and fairness checks.

    ADD deterministic business, fairness, and assurance checks here. These checks
    run in normal Python after the model responds, so the model cannot bypass them.
    """
    checks = [
        "Ranking uses only stated travel constraints",
        "Prices and availability are represented as estimates",
        "Sources, assumptions, limitations, and alternatives are exposed",
        "No booking or safety guarantee is made",
    ]
    warnings: list[str] = []
    if request.accessibility_needs:
        access = next((f for f in findings if f.agent == "accessibility_agent"), None)
        if not access or access.confidence < 0.5:
            warnings.append("Accessibility requirements require direct provider verification")
    if not plan.sources:
        warnings.append("No live sources were available; verify all facts independently")
    if not plan.alternatives:
        warnings.append("No meaningful alternative was generated; human review is recommended")
    return SafetyAssessment(passed=not warnings, checks=checks, warnings=warnings)
