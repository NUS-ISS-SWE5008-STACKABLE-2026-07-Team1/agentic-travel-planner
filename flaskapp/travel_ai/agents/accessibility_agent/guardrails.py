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
from flaskapp.travel_ai.guardrails.fields import collect_free_text
from flaskapp.travel_ai.schemas import AgentFinding, TravelGraphState

RATING_PATTERN = re.compile(r"(?i)(accessibility rating:\s*)([1-5](?:\.\d+)?)(/5)")
EVIDENCE_ID_PATTERN = re.compile(r"\[(E\d+)\]", re.I)
STATUS_PATTERN = re.compile(r"(?i)\bstatus\s*:\s*(verified|unverified|unmet|conflicting)\b")


def screen_accessibility_input(state: TravelGraphState) -> dict[str, Any]:
    """Screen every user-controlled string that can reach this specialist.

    Field selection is delegated to `guardrails.fields.collect_free_text` rather
    than repeated here. The hand-rolled version this replaces read
    `origin_place`/`destination_place`, which are not fields on `TravelRequest`
    — the request carries `origin_city`/`destination_city` (`schemas.py:30-31`).
    Those two `.get()` calls therefore returned None on every real request and
    the city names went unscreened, while the test covering them used the same
    wrong keys and passed. One collector, one place to get it wrong.
    """
    return screen_free_text(collect_free_text(state.get("request", {})))


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


def _set_rating(factors: list[str], rating: int) -> list[str]:
    replacement = f"Accessibility rating: {rating}/5"
    updated = []
    replaced = False
    for factor in factors:
        if RATING_PATTERN.search(factor):
            updated.append(RATING_PATTERN.sub(replacement, factor))
            replaced = True
        else:
            updated.append(factor)
    if not replaced:
        updated.append(replacement)
    return updated


def _option_text(option) -> str:
    return "\n".join([
        option.name, option.description, *option.assumptions,
        *option.limitations, *option.selection_factors,
    ])


def _deterministic_rating(*, grounded: int, requirement_count: int,
                          veto: bool, has_conflict: bool) -> int:
    if veto:
        return 1
    if not grounded:
        return 2
    if has_conflict or grounded < max(1, requirement_count):
        return 3
    # Five is reserved for complete, current, measured evidence. Search
    # excerpts generally cannot establish that by themselves.
    return 4


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
    evidence_by_id = {
        str(item.get("evidence_id", "")).upper(): item
        for item in evidence.get("results", []) if item.get("evidence_id") and item.get("url")
    }
    allowed_urls = {str(item["url"]) for item in evidence_by_id.values()}
    requirement_count = len(evidence.get("search_plan", {}).get("requirements", []))
    removed_urls = 0
    grounded_options = 0
    vetoed_options = 0
    vetoed_names: list[str] = []
    for option in output.options:
        option_text = _option_text(option)
        cited_ids = {item.upper() for item in EVIDENCE_ID_PATTERN.findall(option_text)}
        valid_ids = cited_ids.intersection(evidence_by_id)
        cited_urls = {str(evidence_by_id[item]["url"]) for item in valid_ids}
        original = list(option.source_urls)
        # A URL is grounded only when the option also identifies the exact
        # retrieved evidence record supporting its claim.
        option.source_urls = [url for url in original if url in allowed_urls and url in cited_urls]
        removed_urls += len(original) - len(option.source_urls)
        grounded = len(valid_ids)
        statuses = {item.casefold() for item in STATUS_PATTERN.findall(option_text)}
        veto = "unmet" in statuses or "veto:" in option_text.casefold()
        has_conflict = "conflicting" in statuses
        if veto:
            vetoed_options += 1
            vetoed_names.append(option.name)
            if "VETO: a stated accessibility requirement is unmet." not in option.limitations:
                option.limitations.append("VETO: a stated accessibility requirement is unmet.")
        if option.source_urls and grounded:
            grounded_options += 1
        else:
            if "UNVERIFIED: no supporting retrieved source." not in option.limitations:
                option.limitations.append("UNVERIFIED: no supporting retrieved source.")
        option.selection_factors = _set_rating(
            option.selection_factors,
            _deterministic_rating(
                grounded=grounded, requirement_count=requirement_count,
                veto=veto, has_conflict=has_conflict,
            ),
        )

    if removed_urls:
        output.warnings.append(
            f"Guardrails removed {removed_urls} source URL(s) absent from retrieved evidence."
        )
    rejected = int(evidence.get("rejected_by_guardrails") or 0)
    if rejected:
        output.warnings.append(
            f"Guardrails rejected {rejected} unsafe retrieved evidence result(s)."
        )
    if vetoed_options:
        output.warnings.append(
            "ACCESSIBILITY VETO: removed option(s) with unmet requirements: "
            + ", ".join(vetoed_names)
        )
        output.options = [option for option in output.options if option.name not in vetoed_names]
        output.confidence = min(output.confidence, 0.5)
    if evidence.get("status") not in {"available", "partial"} or not grounded_options:
        output.confidence = min(output.confidence, 0.25)
        output.warnings.append(
            "Accessibility evidence is unavailable or insufficient; verify directly with suppliers."
        )
    elif evidence.get("status") == "partial":
        output.confidence = min(output.confidence, 0.5)
        output.warnings.append(
            "Some accessibility searches failed; treat the assessment as partial."
        )

    requirements = evidence.get("search_plan", {}).get("requirements", [])
    if requirements and (not output.options or grounded_options < len(output.options)):
        questions = []
        for requirement in requirements[:5]:
            description = str(requirement.get("description") or "the stated requirement")
            questions.append(
                f"Supplier confirmation needed: Can you confirm '{description}' and provide specific measurements or policy details?"
            )
        output.warnings.extend(question for question in questions if question not in output.warnings)
    return output
