"""Generic L1 preflight/postprocess hooks for prompt-only specialists.

`make_specialist_node` has taken `preflight` and `postprocess` since it was
written (`agents/base.py:28-29`), but only Accessibility ever passed them, so
Risk & Advisory — a prompt-only agent with no grounding of any kind — ran with
no input or output screening at all. Accessibility's versions are specific to it
(they also enforce evidence provenance and cap unverified ratings); these are
the generic remainder, so wiring an agent up is one import and two arguments.

Deliberately deterministic. These are L1: they reuse the same detectors the
other specialists use, so an agent gains the existing regex gates without adding
a model call per specialist. L2 screens once at the HTTP boundary and once on
the orchestrator's output, which covers this agent's contribution to the plan.

Not re-exported from the package `__init__` on purpose — it imports from
`agents.flight_agent`, and keeping that edge out of the package's import graph
avoids a cycle with the modules under `agents/` that import
`guardrails.fields`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flaskapp.travel_ai.agents.flight_agent.guardrails import (
    screen_input_text, screen_output_text, screen_preferences,
)
from flaskapp.travel_ai.guardrails.fields import collect_free_text
from flaskapp.travel_ai.schemas import AgentFinding, TravelGraphState


def _finding_text(finding: AgentFinding) -> str:
    """Every string in a finding that a traveller could end up reading."""
    values = [finding.summary, *finding.warnings]
    for option in finding.options:
        values.extend([
            option.name, option.description, *option.assumptions,
            *option.limitations, *option.selection_factors,
        ])
    return "\n".join(values)


def make_preflight(agent: str) -> Callable[[TravelGraphState], AgentFinding | None]:
    """Block the model call when traveller free text fails L1 screening.

    Returns None when the input is clean, which `make_specialist_node` reads as
    "proceed". A blocked input yields a zero-confidence finding instead, so the
    orchestrator still receives a well-formed answer and the plan degrades
    rather than failing.
    """

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
            agent=agent,
            summary=f"{agent} analysis was not sent to the model because input screening failed.",
            warnings=[f"Human review required: blocked {', '.join(reasons)}."],
            confidence=0.0,
        )

    return preflight


def make_postprocess(agent: str) -> Callable[[AgentFinding, Any], AgentFinding]:
    """Withhold generated text that fails L1 screening.

    Fails closed the same way `enforce_accessibility_output` does: a flagged
    finding is replaced wholesale rather than edited, because a partially
    redacted answer from a model that just produced biased or injected prose is
    not a trustworthy answer.
    """

    def postprocess(finding: AgentFinding, _context: Any = None) -> AgentFinding:
        text = _finding_text(finding)
        if screen_output_text(text)["flagged"] or screen_preferences([text]):
            return AgentFinding(
                agent=agent,
                summary=f"Generated {agent} analysis was withheld by output guardrails.",
                warnings=["Human review required before acting on this section."],
                confidence=0.0,
            )
        return finding

    return postprocess
