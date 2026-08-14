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

# The phrase a specialist uses to declare that its options are model output
# rather than verified supplier data. Any agent may raise this flag; the
# orchestrator does not get to decide whether it reaches the traveller.
UNVERIFIED_OPTIONS_MARKER = "model estimates"

UNVERIFIED_DISCLOSURE = (
    "The following specialist(s) could not verify their options against real "
    "data and returned model estimates instead: {agents}. Treat every figure, "
    "schedule and availability claim from them as unconfirmed."
)


class SafetyError(ValueError):
    """Raised when a request violates an enforceable safety boundary."""


def screen_prompt(text: Any, max_chars: int) -> str:
    """Screen free-text intake before it reaches the model.

    `validate_request` only sees preferences, accessibility needs and refinement
    notes, none of which exist yet when a traveller types their trip in prose.
    That makes the intake prompt a separate injection surface, so it is screened
    on its own and before any model call rather than after one.
    """
    if not isinstance(text, str) or not text.strip():
        raise SafetyError("A travel request message is required")
    if len(text) > max_chars:
        raise SafetyError("Request is too large")
    if PROMPT_INJECTION.search(text):
        raise SafetyError("Instruction-like text was detected in the message")
    return text.strip()


def screen_answers(answers: Any) -> dict[str, Any]:
    """Reject sensitive ranking traits supplied through the clarification card."""
    if not isinstance(answers, dict):
        raise SafetyError("Answers must be a JSON object")
    forbidden = SENSITIVE_KEYS.intersection(answers)
    if forbidden:
        raise SafetyError(f"Unsupported sensitive fields: {', '.join(sorted(forbidden))}")
    return answers


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


def ungrounded_agents(findings: list[AgentFinding]) -> list[str]:
    """Specialists that flagged their own options as unverified model output."""
    return sorted(
        finding.agent for finding in findings
        if any(UNVERIFIED_OPTIONS_MARKER in warning for warning in finding.warnings)
    )


def enforce_provenance_disclosure(plan: TravelPlan, findings: list[AgentFinding]) -> list[str]:
    """Write an unverified-data flag into the plan's own limitations.

    Observed in end-to-end testing: an agent's `warnings` do not reliably
    survive orchestrator synthesis. A grounded run propagated the per-option
    provenance note verbatim, while an ungrounded one saw the flag paraphrased
    into a milder sentence near the bottom of the plan — semantically close,
    but no longer a signal any code could rely on and easy for a reader to skim
    past.

    Checking whether the model happened to paraphrase it would be a test of
    luck. This appends the statement instead, so disclosure is a property of
    the system rather than of a given generation. Returns the affected agents.
    """
    agents = ungrounded_agents(findings)
    if not agents:
        return []
    disclosure = UNVERIFIED_DISCLOSURE.format(agents=", ".join(agents))
    if disclosure not in plan.limitations:
        plan.limitations = [disclosure, *plan.limitations]
    return agents


def assess_plan(
    request: TravelRequest, plan: TravelPlan, findings: list[AgentFinding]
) -> SafetyAssessment:
    """Run post-generation completeness and fairness checks.

    ADD deterministic business, fairness, and assurance checks here. These checks
    run in normal Python after the model responds, so the model cannot bypass them.

    Note the one deliberate side effect: `plan.limitations` gains an explicit
    unverified-data statement when any specialist flagged its options as model
    estimates. Assessing that disclosure without guaranteeing it would leave the
    traveller relying on the orchestrator's wording — see
    `enforce_provenance_disclosure`.
    """
    checks = [
        "Ranking uses only stated travel constraints",
        "Prices and availability are represented as estimates",
        "Sources, assumptions, limitations, and alternatives are exposed",
        "No booking or safety guarantee is made",
    ]
    warnings: list[str] = []
    unverified = enforce_provenance_disclosure(plan, findings)
    if unverified:
        warnings.append(
            "Options from "
            + ", ".join(unverified)
            + " are unverified model estimates and must be confirmed with the provider"
        )
    else:
        checks.append("Every specialist's options were verified against real data")
    if request.accessibility_needs:
        access = next((f for f in findings if f.agent == "accessibility_agent"), None)
        if not access or access.confidence < 0.5:
            warnings.append("Accessibility requirements require direct provider verification")
    if not plan.sources:
        warnings.append("No live sources were available; verify all facts independently")
    if not plan.alternatives:
        warnings.append("No meaningful alternative was generated; human review is recommended")
    return SafetyAssessment(passed=not warnings, checks=checks, warnings=warnings)
