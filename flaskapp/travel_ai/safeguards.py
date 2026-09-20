"""Deterministic controls around the probabilistic model."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from flaskapp.travel_ai.guardrails.fields import collect_free_text
from flaskapp.travel_ai.guardrails.injection import PROMPT_INJECTION, PromptInjectionGuard
from flaskapp.travel_ai.guardrails.normalization import normalize_for_screening
from flaskapp.travel_ai.guardrails.pii import PiiRedactor, RedactionResult
from flaskapp.travel_ai.guardrails.types import Category, Decision, Verdict
from flaskapp.travel_ai.schemas import AgentFinding, SafetyAssessment, TravelPlan, TravelRequest

# Re-exported, not defined here. The rules moved to `guardrails/injection.py`
# when the set outgrew one regex, but three readers still reach for this name:
# `validate_request` below, the admin prompt catalog (`api.py`, which renders
# `.pattern`), and the eval harness's L1 attribution.
_INJECTION_GUARD = PromptInjectionGuard()
_DEFAULT_REDACTOR = PiiRedactor()

# The full specialist roster. Declared here rather than imported from `graph`:
# safeguards is the deterministic layer and must not depend on how the workflow
# happens to be composed. `tests/test_schema_parity.py` guards the two lists.
SPECIALIST_NAMES = (
    "flight_agent", "hotel_transport_agent", "accessibility_agent", "risk_advisory_agent",
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


@dataclass(frozen=True)
class ScreenedPrompt:
    """What survived screening, and the evidence of what each layer did.

    `text` is the redacted prompt and is the only version anything downstream
    should use — it is what gets persisted, what the extraction model sees, and
    what the traveller reads back in the transcript.
    """

    text: str
    pii: RedactionResult
    verdict: Verdict | None = None


def screen_prompt(
    text: Any, max_chars: int, guardrail: Any = None, redactor: Any = None
) -> ScreenedPrompt:
    """Screen free-text intake before it reaches the model.

    `validate_request` only sees preferences, accessibility needs and refinement
    notes, none of which exist yet when a traveller types their trip in prose.
    That makes the intake prompt a separate injection surface, so it is screened
    on its own and before any model call rather than after one.

    Three layers, in this order: length and injection (free, local, and either
    can reject outright), then PII redaction, then L2.

    Redaction sits ahead of L2 deliberately. L2 is a network call to a model
    provider, so redacting after it would mean the one component whose job is to
    notice PII is also the component that transmits it. Injection detection runs
    ahead of redaction only because it is free and its outcome is a rejection —
    the two rule sets are disjoint, so that order is a cost choice, not a
    correctness one.

    Returns a `ScreenedPrompt`. Callers must use `.text`, never the argument
    they passed in: the redacted string is the one safe to store, send and echo.
    """
    if not isinstance(text, str) or not text.strip():
        raise SafetyError("A travel request message is required")
    if len(text) > max_chars:
        raise SafetyError("Request is too large")
    # Detection runs against a normalized copy — homoglyphs and zero-width
    # characters folded to what they visually read as — so an attacker who
    # types "іgnore previous instructions" with a Cyrillic і cannot dodge the
    # regex that way. The original `text`, not the normalized copy, is what
    # gets redacted and stored below: normalization is for detection only and
    # must never change what a traveller's own free text actually says.
    if _INJECTION_GUARD.detect(normalize_for_screening(text)):
        raise SafetyError("Instruction-like text was detected in the message")
    # Redaction sits here, between the free local check and the network one, so
    # no identifier the traveller typed ever reaches a model provider.
    result = (redactor if redactor is not None else _DEFAULT_REDACTOR).redact(text.strip())
    return ScreenedPrompt(
        text=result.text, pii=result, verdict=_screen_redacted(result, guardrail)
    )


def _screen_redacted(result: RedactionResult, guardrail: Any) -> Verdict | None:
    """L2 over redacted text, with one verdict the caller must not act on.

    Observed live: a traveller wrote "I use my credit card <number> for
    memberships". Redaction masked the number, and L2 then blocked the masked
    sentence as `pii_exposure` at confidence 1.0 — reading the intent still
    visible in the words around the placeholder, not any data, because the data
    was already gone. The traveller was denied over information the system no
    longer held, which is precisely what "redaction never blocks" exists to
    prevent.

    So a `pii_exposure` block is downgraded to FLAG — audited, not denied — but
    ONLY when redaction actually fired on this text. That narrowness is what
    makes it safe: if nothing was redacted, L2 is reporting a credential these
    rules do not cover (an API key, say), that block is about live data, and it
    stands. Every other category is untouched.
    """
    try:
        return _apply_l2([result.text], guardrail)
    except GuardrailBlocked as exc:
        if not (result.redacted and exc.verdict.category is Category.PII_EXPOSURE):
            raise
        return replace(exc.verdict, decision=Decision.FLAG)


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
    # `guardrails/fields.py`. Each value is normalized before matching, for the
    # same reason as `screen_prompt` above — this only widens what the existing
    # regex catches, it does not change what is validated or stored.
    if any(PROMPT_INJECTION.search(normalize_for_screening(value)) for value in collect_free_text(
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


UNCONSULTED_DISCLOSURE = (
    "The following specialist(s) were not consulted for this plan, because the "
    "request did not ask for them: {agents}. Nothing here reflects their input."
)


def disclose_unconsulted(plan: TravelPlan, selected) -> list[str]:
    """Name the specialists that never ran, in the plan's own limitations.

    Absence is not a signal a traveller can read. A plan that never mentions
    flights looks identical whether the flight agent searched and found nothing,
    or was never asked at all — and only one of those is a reason to go looking
    elsewhere.

    Written deterministically for the same reason as
    `enforce_provenance_disclosure`: the orchestrator cannot be relied on to
    mention an agent that produced nothing for it to mention. Returns the
    unconsulted agents so the caller can record them.
    """
    unconsulted = sorted(set(SPECIALIST_NAMES) - set(selected))
    if not unconsulted:
        return []
    disclosure = UNCONSULTED_DISCLOSURE.format(agents=", ".join(unconsulted))
    if disclosure not in plan.limitations:
        plan.limitations = [disclosure, *plan.limitations]
    return unconsulted


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
    has_accessibility_needs = bool(
        request.accessibility_needs
        or any(request.traveller_accessibility_needs)
    )
    if has_accessibility_needs:
        access = next((f for f in findings if f.agent == "accessibility_agent"), None)
        if not access or access.confidence < 0.5:
            warnings.append("Accessibility requirements require direct provider verification")
    if not plan.sources:
        warnings.append("No live sources were available; verify all facts independently")
    if not plan.alternatives:
        warnings.append("No meaningful alternative was generated; human review is recommended")
    return SafetyAssessment(passed=not warnings, checks=checks, warnings=warnings)
