"""Risk & Advisory Agent's 'brain' — reasons over `domain.py`'s already-grounded
output. Never queries the reference data itself and never invents a risk the
tool didn't already find.

The judgement that makes this an agent rather than a lookup (the litmus test
in `Architecture_v2.md` §3): picking which of `domain.py`'s items matter most,
and connecting more than one of them into a single insight — a seasonal
window and a dated event that overlap becoming one warning about compounded
risk, not two separate ones. `seed_data.py` deliberately engineered a few such
overlaps (Tokyo's Obon holiday inside typhoon season; Washington's
Independence Day inside hurricane-remnant season) so this has real cases to
demonstrate on, not just a hypothetical.

Fail-closed by construction, matching Flight/Hotel's shape: a fabricated
citation, a screening failure, or a missed data-backed escalation triggers a
retry, then a fallback that renders `domain.py`'s own items with no
narrative — never trusts an LLM response that failed one of these checks.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from flaskapp.travel_ai.agents.risk_advisory_agent.guardrails import (
    expected_escalation_reasons,
    missing_escalation,
    screen_input,
    screen_output,
    validate_grounded_response,
)
from flaskapp.travel_ai.agents.risk_advisory_agent.prompt import RISK_ADVISORY_SYSTEM_PROMPT
from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import (
    RiskAgentResponse,
    RiskProposal,
    RiskProposalRequest,
)

MAX_ATTEMPTS = 2

FALLBACK_RATIONALE = (
    "Automated narrative unavailable this round; showing the underlying reference "
    "data directly. Every item listed is still fully grounded in that reference data."
)


class StructuredLLM(Protocol):
    def invoke(self, messages: list[Any]) -> RiskAgentResponse: ...


class ChatModel(Protocol):
    def with_structured_output(self, schema: type, method: str) -> StructuredLLM: ...


def _compact(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _build_messages(request: RiskProposalRequest, proposal: RiskProposal) -> list[dict]:
    """`preferences`/`refinement_notes` are included here, not just screened
    and discarded: they are the only signal the model has of what the
    traveller actually cares about, and reaching this function at all already
    means `run_risk_agent` cleared them through `screen_input`."""
    return [
        {"role": "system", "content": RISK_ADVISORY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _compact({
                "destination": request.destination,
                "departure_date": str(request.departure_date),
                "return_date": str(request.return_date),
                "traveller_preferences": request.preferences,
                "traveller_refinement_notes": request.refinement_notes,
                "risk_items": [item.model_dump() for item in proposal.items],
            }),
        },
    ]


def _fallback_response(proposal: RiskProposal) -> RiskAgentResponse:
    reasons = expected_escalation_reasons(proposal)
    return RiskAgentResponse(
        rationale=FALLBACK_RATIONALE,
        highlighted_risk_ids=[item.risk_id for item in proposal.items],
        escalate=bool(reasons),
        escalation_reason="; ".join(reasons) if reasons else None,
        confidence=0.0,
    )


def _blocked_input_response(proposal: RiskProposal, screen_result: dict) -> RiskAgentResponse:
    reasons = []
    if screen_result["injection"]:
        reasons.append("instruction-like content")
    if screen_result["high_bias"]:
        reasons.append("high-risk generalising content")
    if screen_result["toxicity"]:
        reasons.append("toxic content")
    reason_text = ", ".join(reasons) or "an input policy violation"
    return RiskAgentResponse(
        rationale=(
            f"Traveller-stated preferences were not sent to the reasoning model because "
            f"input screening flagged {reason_text}. Showing reference data directly; a "
            "human should review the original request."
        ),
        highlighted_risk_ids=[item.risk_id for item in proposal.items],
        escalate=True,
        escalation_reason=f"Input screening blocked this request: {reason_text}.",
        confidence=0.0,
    )


def _reason_over_proposal(
    request: RiskProposalRequest, proposal: RiskProposal, llm: ChatModel, tracer=None,
) -> RiskAgentResponse:
    structured_llm = llm.with_structured_output(RiskAgentResponse, method="json_schema")
    messages = _build_messages(request, proposal)

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = structured_llm.invoke(messages)
        except Exception as exc:  # noqa: BLE001 - any LLM failure falls back, never propagates
            if tracer is not None:
                tracer.record("agent_llm_attempt_failed", "risk_advisory_agent", {
                    "attempt": attempt, "error_type": type(exc).__name__,
                })
            continue

        offending = validate_grounded_response(response, proposal)
        output_screen = screen_output(response.rationale)
        missing = missing_escalation(response, proposal)
        if not offending and not output_screen["flagged"] and not missing:
            return response

        if tracer is not None:
            details: dict[str, Any] = {"attempt": attempt}
            if offending:
                details["ungrounded_risk_ids"] = offending
            if output_screen["flagged"]:
                details["output_policy_violation"] = {
                    "injection": output_screen["injection"], "bias": output_screen["bias"]["risk_level"],
                    "toxicity": output_screen["toxicity"],
                }
            if missing:
                details["missing_escalation"] = missing
            tracer.record("agent_llm_attempt_failed", "risk_advisory_agent", details)

    return _fallback_response(proposal)


def run_risk_agent(
    request: RiskProposalRequest, proposal: RiskProposal, llm: ChatModel, *, tracer=None,
) -> RiskAgentResponse:
    """The tool (`propose_risks`, called by `agent.py` before this) already
    ran; this reasons over its output, fail-closed."""
    if tracer is not None:
        tracer.record("agent_started", "risk_advisory_agent", {"item_count": len(proposal.items)})

    input_screen = screen_input([*request.preferences, *request.refinement_notes])
    if input_screen["blocked"]:
        if tracer is not None:
            tracer.record("agent_input_blocked", "risk_advisory_agent", {
                "injection_count": len(input_screen["injection"]),
                "high_bias_count": len(input_screen["high_bias"]),
                "toxicity_count": len(input_screen["toxicity"]),
            })
        response = _blocked_input_response(proposal, input_screen)
        if tracer is not None:
            tracer.record("agent_completed", "risk_advisory_agent", {
                "escalate": response.escalate, "confidence": response.confidence,
            })
        return response

    response = _reason_over_proposal(request, proposal, llm, tracer)
    if tracer is not None:
        tracer.record("agent_completed", "risk_advisory_agent", {
            "escalate": response.escalate, "confidence": response.confidence,
        })
    return response
