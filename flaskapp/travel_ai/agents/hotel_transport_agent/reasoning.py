"""Hotel & Transport Agent's 'brain' — the LLM reasoning layer wrapping domain.py's
deterministic tool.

Architecture: the tool (`propose_hotels`/`propose_transport`) always runs
first and is never bypassed — search/filter/rank stays deterministic,
reliable, and cheap. The LLM only reasons over the tool's already-grounded
output: writing the traveller-facing rationale and highlighting the best options.
Fail-closed by construction: a hallucinated hotel_id, an LLM exception, or an
invalid structured response never reaches the caller — retry once, then fall
back to the deterministic proposal with no narrative rather than trust
ungrounded output.
"""

from __future__ import annotations

import json
from typing import Any

from flaskapp.travel_ai.agents.hotel_transport_agent.domain import (
    propose_hotels,
    propose_transport,
    screen_hotels,
)
from flaskapp.travel_ai.agents.hotel_transport_agent.guardrails import (
    screen_input_text,
    screen_output_text,
    validate_grounded_explanation,
)
from flaskapp.travel_ai.agents.hotel_transport_agent.prompt import (
    HOTEL_TRANSPORT_SYSTEM_PROMPT,
)
from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import (
    HotelProposal,
    HotelProposalRequest,
    HotelTransportResponse,
    TransportOption,
)

FALLBACK_RATIONALE = (
    "Automated explanation unavailable this round; showing ranked candidates "
    "without narrative rationale. All listed options are still fully grounded "
    "in real inventory."
)
MAX_ATTEMPTS = 2


class StructuredLLM:
    """The narrow shape reasoning.py needs from a LangChain chat model."""

    def __init__(self, llm):
        self._llm = llm

    def invoke(self, messages: list[Any]) -> HotelTransportResponse:
        return self._llm.with_structured_output(HotelTransportResponse, method="json_schema").invoke(messages)


def _compact(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _build_messages(
    request: HotelProposalRequest,
    hotel_proposal: HotelProposal,
    screening: list,
    transport_options: list[TransportOption],
) -> list[dict]:
    return [
        {"role": "system", "content": HOTEL_TRANSPORT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _compact({
                "trip_context": request.trip_context.model_dump(),
                "constraints": request.constraints.model_dump() if request.constraints else None,
                "flight_candidates": request.flight_candidates,
                "hotel_proposal": hotel_proposal.model_dump(),
                "hotel_screening": [s.model_dump() for s in screening],
                "transport_options": [opt.model_dump() for opt in transport_options],
            }),
        },
    ]


def _fallback_response(hotel_proposal: HotelProposal) -> HotelTransportResponse:
    return HotelTransportResponse(
        rationale=FALLBACK_RATIONALE,
        highlighted_hotel_ids=[c.hotel_id for c in hotel_proposal.candidates],
        confidence=0.0,
    )


def _blocked_input_response(hotel_proposal: HotelProposal, screen_result: dict) -> HotelTransportResponse:
    reasons = []
    if screen_result["injection"]:
        reasons.append("instruction-like content")
    if screen_result["high_bias"]:
        reasons.append("high-risk biased/stereotyping content")
    if screen_result["toxicity"]:
        reasons.append("toxic content")
    reason_text = ", ".join(reasons) or "an input policy violation"
    return HotelTransportResponse(
        rationale=(
            "Traveller-stated preferences were not sent to the reasoning model because "
            f"input screening flagged {reason_text}. Showing ranked candidates without "
            "narrative rationale; a human should review the original request."
        ),
        highlighted_hotel_ids=[c.hotel_id for c in hotel_proposal.candidates],
        escalate=True,
        escalation_reason=f"Input screening blocked this request: {reason_text}.",
        confidence=0.0,
    )


def _reason_over_proposal(
    request: HotelProposalRequest,
    hotel_proposal: HotelProposal,
    screening: list,
    transport_options: list[TransportOption],
    llm: StructuredLLM,
    tracer=None,
) -> HotelTransportResponse:
    """The retry-then-fallback loop."""
    messages = _build_messages(request, hotel_proposal, screening, transport_options)

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = llm.invoke(messages)
        except Exception as exc:
            if tracer is not None:
                tracer.record("agent_llm_attempt_failed", "hotel_transport_agent", {
                    "attempt": attempt, "error_type": type(exc).__name__,
                })
            continue

        offending = validate_grounded_explanation(response.highlighted_hotel_ids, hotel_proposal)
        output_screen = screen_output_text(response.rationale)
        if not offending and not output_screen["flagged"]:
            return response

        if tracer is not None:
            details: dict[str, Any] = {"attempt": attempt}
            if offending:
                details["ungrounded_hotel_ids"] = offending
            if output_screen["flagged"]:
                details["output_policy_violation"] = {
                    "bias": output_screen["bias"], "toxicity": output_screen["toxicity"],
                }
            tracer.record("agent_llm_attempt_failed", "hotel_transport_agent", details)

    return _fallback_response(hotel_proposal)


def run_hotel_agent(
    request: HotelProposalRequest,
    hotel_inventory: list[Any],
    transport_options: list[TransportOption],
    llm: StructuredLLM,
    *,
    tracer=None,
) -> tuple[HotelProposal, list[TransportOption], HotelTransportResponse]:
    """The tool always runs; the brain reasons over its output, fail-closed."""
    hotel_proposal = propose_hotels(request, hotel_inventory)
    screening = screen_hotels(request, hotel_inventory, request.trip_context.dest_city_slug)
    ranked_transport = propose_transport(request, transport_options)

    if tracer is not None:
        tracer.record("agent_started", "hotel_transport_agent", {
            "hotel_candidate_count": len(hotel_proposal.candidates),
            "transport_option_count": len(ranked_transport),
        })

    input_screen = screen_input_text(
        request.trip_context.preferences, request.trip_context.refinement_notes
    )
    if input_screen["blocked"]:
        if tracer is not None:
            tracer.record("agent_input_blocked", "hotel_transport_agent", {
                "injection_count": len(input_screen["injection"]),
                "high_bias_count": len(input_screen["high_bias"]),
                "toxicity_count": len(input_screen["toxicity"]),
            })
        response = _blocked_input_response(hotel_proposal, input_screen)
        if tracer is not None:
            tracer.record("agent_completed", "hotel_transport_agent", {
                "escalate": response.escalate, "confidence": response.confidence,
            })
        return hotel_proposal, ranked_transport, response

    response = _reason_over_proposal(
        request, hotel_proposal, screening, ranked_transport, llm, tracer
    )

    if tracer is not None:
        tracer.record("agent_completed", "hotel_transport_agent", {
            "escalate": response.escalate,
            "confidence": response.confidence,
        })
    return hotel_proposal, ranked_transport, response
