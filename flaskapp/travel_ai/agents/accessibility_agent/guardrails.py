"""Accessibility Agent input, evidence, and output guardrails.

The design follows Flight Agent's symmetric screening: traveller free text is
checked before any model call and generated text is checked before it is trusted.
Accessibility adds provenance enforcement because its claims must be grounded in
the retrieved evidence bundle.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from flaskapp.travel_ai.agents.flight_agent.guardrails import (
    screen_input_text as screen_free_text,
    screen_output_text,
    screen_preferences,
)
from flaskapp.travel_ai.schemas import AgentFinding, TravelGraphState

RATING_PATTERN = re.compile(r"(?i)(accessibility rating:\s*)([1-5](?:\.\d+)?)(/5)")


def screen_accessibility_input(state: TravelGraphState) -> dict[str, Any]:
    """Screen every user-controlled string that can reach this specialist."""
    request = state.get("request", {})
    per_traveller = [
        need
        for needs in request.get("traveller_accessibility_needs", [])
        for need in (needs or [])
        if isinstance(need, str)
    ]
    places = [
        value for value in (request.get("origin_place"), request.get("destination_place"))
        if isinstance(value, str)
    ]
    return screen_free_text(
        request.get("preferences"),
        request.get("accessibility_needs", []),
        request.get("refinement_notes", []),
        per_traveller,
        places,
    )


def blocked_input_finding(state: TravelGraphState) -> AgentFinding | None:
    """Return a safe deterministic finding when pre-model screening blocks input."""
    result = screen_accessibility_input(state)
    if not result["blocked"]:
        return None
    reasons = []
    if result["injection"]:
        reasons.append("instruction-like content")
    if result["high_bias"]:
        reasons.append("high-risk biased or stereotyping content")
    if result["toxicity"]:
        reasons.append("toxic content")
    reason = ", ".join(reasons)
    return AgentFinding(
        agent="accessibility_agent",
        summary="Accessibility analysis was not sent to the model because input screening failed.",
        warnings=[f"Human review required: blocked {reason}."],
        confidence=0.0,
    )


def sanitize_evidence(evidence: dict) -> dict:
    """Remove retrieved snippets containing injection, high bias, or toxicity."""
    cleaned = deepcopy(evidence)
    accepted = []
    rejected = 0
    for item in cleaned.get("results", []):
        texts = [str(item.get("title") or ""), str(item.get("excerpt") or "")]
        if screen_free_text(texts)["blocked"]:
            rejected += 1
            continue
        accepted.append(item)
    cleaned["results"] = accepted
    cleaned["rejected_by_guardrails"] = rejected
    if cleaned.get("status") == "available" and not accepted:
        cleaned["status"] = "no_results"
    return cleaned


def _all_output_text(finding: AgentFinding) -> str:
    values = [finding.summary, *finding.warnings]
    for option in finding.options:
        values.extend([
            option.name, option.description, *option.assumptions,
            *option.limitations, *option.selection_factors,
        ])
    return "\n".join(values)


def _cap_unverified_rating(factor: str) -> str:
    def replace(match: re.Match) -> str:
        rating = float(match.group(2))
        return match.group(0) if rating <= 2 else f"{match.group(1)}2/5"
    return RATING_PATTERN.sub(replace, factor)


def enforce_accessibility_output(finding: AgentFinding, evidence: Any) -> AgentFinding:
    """Fail closed on unsafe prose and remove claims not grounded by evidence URLs."""
    output = finding.model_copy(deep=True)
    text = _all_output_text(output)
    policy = screen_output_text(text)
    injection = bool(screen_preferences([text]))
    if policy["flagged"] or injection:
        return AgentFinding(
            agent="accessibility_agent",
            summary="Generated accessibility analysis was withheld by output guardrails.",
            warnings=["Human accessibility review required before booking."],
            confidence=0.0,
        )

    evidence = evidence if isinstance(evidence, dict) else {}
    allowed_urls = {
        str(item.get("url")) for item in evidence.get("results", []) if item.get("url")
    }
    removed_urls = 0
    grounded_options = 0
    for option in output.options:
        original = list(option.source_urls)
        option.source_urls = [url for url in original if url in allowed_urls]
        removed_urls += len(original) - len(option.source_urls)
        if option.source_urls:
            grounded_options += 1
        else:
            option.selection_factors = [
                _cap_unverified_rating(factor) for factor in option.selection_factors
            ]
            if "UNVERIFIED: no supporting retrieved source." not in option.limitations:
                option.limitations.append("UNVERIFIED: no supporting retrieved source.")

    if removed_urls:
        output.warnings.append(
            f"Guardrails removed {removed_urls} source URL(s) absent from retrieved evidence."
        )
    rejected = int(evidence.get("rejected_by_guardrails") or 0)
    if rejected:
        output.warnings.append(
            f"Guardrails rejected {rejected} unsafe retrieved evidence result(s)."
        )
    if evidence.get("status") != "available" or not grounded_options:
        output.confidence = min(output.confidence, 0.25)
        output.warnings.append(
            "Accessibility evidence is unavailable or insufficient; verify directly with suppliers."
        )
    return output
