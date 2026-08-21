"""Deterministic controls around the probabilistic model."""

from __future__ import annotations

import re
from typing import Any

from flaskapp.travel_ai.guardrails.fields import collect_free_text
from flaskapp.travel_ai.guardrails.types import Decision, Verdict
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


class GuardrailBlocked(SafetyError):
    """Raised when the L2 classifier blocks. Carries the verdict for auditing.

    A subclass rather than a new exception type so every existing
    `except SafetyError` — the 422 handlers in `api.py:73,91,110` among them —
    keeps working unchanged and the traveller sees the same response shape.
    """

    def __init__(self, message: str, verdict: Verdict):
        super().__init__(message)
        self.verdict = verdict


# Deliberately vague, and the same sentence for every category. A classifier
# that explains which rule it tripped is a free oracle for tuning an attack
# against it; the specific reason goes to the audit trail instead, where the
# operator can see it and the attacker cannot.
_BLOCKED_MESSAGE = "This request could not be processed. Please rephrase your travel details."


def _apply_l2(texts: list[str], guardrail: Any) -> Verdict | None:
    """Run the L2 classifier over already L0/L1-clean text.

    Returns the verdict (ALLOW or FLAG) so the caller can record it, and raises
    on BLOCK. A FLAG is deliberately not an error: it is a low-confidence
    suspicion that belongs in the trace, not a denial served to a traveller.
    """
    if guardrail is None:
        return None
    verdict = guardrail.screen_input(texts)
    if verdict.decision is Decision.BLOCK:
        raise GuardrailBlocked(_BLOCKED_MESSAGE, verdict)
    return verdict


def screen_prompt(text: Any, max_chars: int, guardrail: Any = None) -> str:
    """Screen free-text intake before it reaches the model.

    `validate_request` only sees preferences, accessibility needs and refinement
    notes, none of which exist yet when a traveller types their trip in prose.
    That makes the intake prompt a separate injection surface, so it is screened
    on its own and before any model call rather than after one.

    L2 runs last and only on text the deterministic checks already cleared, so
    an oversized or obviously-injected prompt is still rejected without an API
    call. This surface needs L2 most: it is unconstrained prose, where the
    four literal patterns in `PROMPT_INJECTION` have the least purchase.
    """
    if not isinstance(text, str) or not text.strip():
        raise SafetyError("A travel request message is required")
    if len(text) > max_chars:
        raise SafetyError("Request is too large")
    if PROMPT_INJECTION.search(text):
        raise SafetyError("Instruction-like text was detected in the message")
    _apply_l2([text], guardrail)
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
    """Reject oversized, injected, or explicitly sensitive ranking inputs.

    L0 and L1 only. The L2 classifier is a separate call — `screen_request_l2`
    — so that this function stays what it has always been: the deterministic
    gate, testable and reviewable without a model anywhere near it.
    """
    if len(str(payload)) > max_chars:
        raise SafetyError("Request is too large")
    forbidden = SENSITIVE_KEYS.intersection(payload)
    if forbidden:
        raise SafetyError(f"Unsupported sensitive fields: {', '.join(sorted(forbidden))}")
    request = TravelRequest.model_validate(payload)
    # `collect_free_text` covers the city names and per-traveller accessibility
    # needs too, which the old inline concatenation missed — see the note in
    # `guardrails/fields.py`.
    if any(PROMPT_INJECTION.search(value) for value in collect_free_text(
        request.model_dump(mode="json")
    )):
        raise SafetyError("Instruction-like text was detected in request fields")
    return request


def screen_request_l2(request: TravelRequest, guardrail: Any = None) -> Verdict | None:
    """Semantic screening of an already L0/L1-clean request.

    Returns None when no classifier was supplied, otherwise an ALLOW or FLAG
    verdict; a BLOCK raises `GuardrailBlocked`. The verdict is returned rather
    than discarded so the caller can record it — a trace showing the classifier
    ran and allowed is as much evidence as one showing it blocked.

    Kept separate from `validate_request` on purpose. The deterministic gate
    runs first and can reject on its own, so this only ever costs an API call
    for input that is already structurally sound and syntactically clean.
    """
    return _apply_l2(collect_free_text(request.model_dump(mode="json")), guardrail)


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
