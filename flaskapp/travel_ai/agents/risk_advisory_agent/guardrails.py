"""Risk & Advisory Agent guardrails — input/output screening plus grounding.

`preflight`/`postprocess` wire this agent's node to the shared L1 detectors
(`guardrails.detectors`, a top-level module owned by no single agent)
directly, rather than through `guardrails.specialist`'s generic hooks, so
this module owns its own screening without depending on another agent's
folder for it.

Note: `guardrails.detectors` currently duplicates the same pattern lists
that live in `flight_agent/guardrails.py` (still the source `accessibility_agent`
and `guardrails.specialist` import). Repointing those onto this module too
would remove that duplication, but that's a cross-agent change outside this
agent's own scope -- left for the team to do together, not unilaterally here.

What is specific to this agent is whether ITS OWN OUTPUT keeps the two
promises `prompt.py` makes:

1. Never present a destination's risk facts as sourced when this agent has no
   reference profile for it (`seed_data.get_profile` returned None).
2. Always prefix a warning with "ESCALATE:" when the reference data itself
   says the situation is severe -- a high-severity seasonal risk window
   overlapping the trip, or a safety_advisory_level of "reconsider_travel" /
   "do_not_travel".

Severity is judged against `seed_data.py`, not against the model's own
wording. Keyword-matching the model's prose for "critical"/"severe" would
only catch the model successfully describing its own output; checking against
the reference data catches the failure mode that actually matters here --
the model understating a risk the data says is serious.
"""

from __future__ import annotations

from datetime import date

from flaskapp.travel_ai.agents.risk_advisory_agent.seed_data import (
    active_seasonal_risks,
    get_profile,
)
from flaskapp.travel_ai.guardrails.detectors import (
    screen_input_text,
    screen_output_text,
    screen_preferences,
)
from flaskapp.travel_ai.guardrails.fields import collect_free_text
from flaskapp.travel_ai.schemas import AgentFinding, TravelGraphState

NAME = "risk_advisory_agent"
ESCALATE_PREFIX = "ESCALATE:"

# safety_advisory_level values (seed_data.AdvisoryLevel) severe enough that any
# finding for that destination must escalate, independent of trip dates.
SEVERE_ADVISORY_LEVELS = {"reconsider_travel", "do_not_travel"}

NO_DATA_MARKERS = (
    "no reference data",
    "no data available",
    "insufficient data",
    "unable to verify",
    "cannot verify",
)


def ungrounded_destination(finding: AgentFinding, destination: str) -> bool:
    """True when this finding makes claims about a destination with no reference profile.

    A finding for a destination outside `seed_data.COUNTRY_RISK_PROFILES`
    should say so plainly (one of `NO_DATA_MARKERS`) rather than presenting
    specific risk facts as if they were sourced.
    """
    if get_profile(destination) is not None:
        return False
    if not (finding.warnings or finding.options):
        return False
    texts = [finding.summary, *finding.warnings]
    return not any(marker in text.lower() for marker in NO_DATA_MARKERS for text in texts)


def expected_escalation_reasons(
    destination: str, departure_date: date, return_date: date
) -> list[str]:
    """Why this trip should produce at least one ESCALATE: warning, per reference data.

    Empty list means the reference data does not require escalation -- either
    the destination has no profile (nothing to escalate from) or nothing in
    it is severe for these dates.
    """
    profile = get_profile(destination)
    if profile is None:
        return []
    reasons = []
    if profile.safety_advisory_level in SEVERE_ADVISORY_LEVELS:
        reasons.append(f"safety advisory level is '{profile.safety_advisory_level}'")
    reasons.extend(
        f"seasonal risk '{risk.label}' is high-severity for these travel dates"
        for risk in active_seasonal_risks(profile, departure_date, return_date)
        if risk.severity == "high"
    )
    return reasons


def missing_escalation(
    finding: AgentFinding, destination: str, departure_date: date, return_date: date
) -> list[str]:
    """Reference-data-backed reasons this finding should have escalated but didn't.

    Empty list = clean: either nothing required escalation, or the finding
    already carries an ESCALATE:-prefixed warning.
    """
    reasons = expected_escalation_reasons(destination, departure_date, return_date)
    if not reasons:
        return []
    if any(warning.startswith(ESCALATE_PREFIX) for warning in finding.warnings):
        return []
    return reasons


def validate_finding(
    finding: AgentFinding, destination: str, departure_date: date, return_date: date
) -> list[str]:
    """All grounding/escalation violations found in one finding. Empty = clean."""
    violations = []
    if ungrounded_destination(finding, destination):
        violations.append(
            f"No reference data available for '{destination}'; risk claims are unverified "
            "and the finding should have said so"
        )
    missing = missing_escalation(finding, destination, departure_date, return_date)
    if missing:
        violations.append(
            "Missing 'ESCALATE:' warning despite reference data indicating: "
            + "; ".join(missing)
        )
    return violations


def _finding_text(finding: AgentFinding) -> str:
    values = [finding.summary, *finding.warnings]
    for option in finding.options:
        values.extend([
            option.name, option.description, *option.assumptions,
            *option.limitations, *option.selection_factors,
        ])
    return "\n".join(values)


def preflight(state: TravelGraphState) -> AgentFinding | None:
    result = screen_input_text(collect_free_text(state.get("request", {})))
    if not result["blocked"]:
        return None
    reasons = []
    if result["injection"]:
        reasons.append("instruction-like content")
    if result["high_bias"]:
        reasons.append("high-risk biased or stereotyping content")
    if result["toxicity"]:
        reasons.append("toxic content")
    return AgentFinding(
        agent=NAME,
        summary=f"{NAME} analysis was not sent to the model because input screening failed.",
        warnings=[f"Human review required: blocked {', '.join(reasons)}."],
        confidence=0.0,
    )


def trip_context(state: TravelGraphState) -> tuple[str | None, date, date]:
    request = state.get("request", {})
    return (
        request.get("destination"),
        date.fromisoformat(request["departure_date"]),
        date.fromisoformat(request["return_date"]),
    )


def postprocess(
    finding: AgentFinding, context: tuple[str | None, date, date]
) -> AgentFinding:
    text = _finding_text(finding)
    if screen_output_text(text)["flagged"] or screen_preferences([text]):
        return AgentFinding(
            agent=NAME,
            summary=f"Generated {NAME} analysis was withheld by output guardrails.",
            warnings=["Human review required before acting on this section."],
            confidence=0.0,
        )

    destination, departure_date, return_date = context
    if ungrounded_destination(finding, destination):
        return AgentFinding(
            agent=NAME,
            summary=f"Generated {NAME} analysis was withheld: unverifiable destination claims.",
            warnings=[
                f"No reference data available for '{destination}'; the model's claims could "
                "not be verified and the finding did not say so."
            ],
            confidence=0.0,
        )

    missing = missing_escalation(finding, destination, departure_date, return_date)
    if missing:
        return finding.model_copy(update={
            "warnings": [f"{ESCALATE_PREFIX} " + "; ".join(missing), *finding.warnings]
        })
    return finding
