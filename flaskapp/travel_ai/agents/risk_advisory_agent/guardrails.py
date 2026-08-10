"""Risk & Advisory Agent guardrails — output grounding gate.

Prompt-injection/bias/toxicity screening of the traveller's own free text
already happens once, upstream, for every agent
(`flaskapp.travel_ai.safeguards.validate_request`) before any specialist runs.
Re-checking that here would just repeat the same regexes for no benefit.

What is specific to this agent, and not covered anywhere else, is whether ITS
OWN OUTPUT keeps the two promises `prompt.py` makes:

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
from flaskapp.travel_ai.schemas import AgentFinding

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
