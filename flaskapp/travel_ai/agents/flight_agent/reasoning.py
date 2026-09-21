"""Flight Agent's 'brain' — the LLM reasoning layer wrapping domain.py's
deterministic tool.

Architecture (see localfolder/update.md session notes, 2026-07-19, for the
full reasoning): the tool (`propose_flights`/`screen_flights`) always runs
first and is never bypassed — search/filter/rank stays deterministic,
reliable, and cheap. The LLM only reasons over the tool's already-grounded
output: writing the traveller-facing rationale, judging whether to escalate,
and — option B, localfolder/discussion_agents_vs_deterministic.md §1 — proposing (never
deciding) that one of Flight Agent's *own* soft preferences be acknowledged as
unmet, when every surviving flight already fails it. This keeps Flight Agent at the
"structured-output" pattern (llmops_plan.md §8), not a full autonomous
tool-calling loop — matches the course's own "pick the lowest agency level
that solves the problem" guidance.

Fail-closed by construction: a hallucinated flight_id, an LLM exception, or
an invalid structured response never reaches the caller — retry once, then
fall back to the deterministic proposal with no narrative rather than trust
ungrounded output. The tool's output is always usable on its own even if the
LLM call fails entirely, which is deliberate: Flight Agent degrades to
workflow-level reliability, never blocks a negotiation on the LLM.

Option B's fence, enforced in code (not just the prompt): an acknowledgment
only ever takes effect if (1) `PreferenceAcknowledgment.field` is one of the
three soft preferences — the schema itself can't express anything else — and
(2) `domain.acknowledgment_is_valid()` re-confirms a real, unanimous gap
exists, never trusting the LLM's own claim. At most one extra tool call and
one extra LLM call per run — bounded, not a loop. Applying it never changes
which flights are shown; only that the traveller is told the wish went unmet.

Input/output screening (2026-07-20, guardrails.py): `trip_context.preferences`
(the only free text this agent ever receives) is screened for injection/bias/
toxicity BEFORE it is ever sent to the LLM — a blocked input never reaches the
model at all, the tool's proposal is still returned but with a fallback
response, not a "handled" one. The LLM's own rationale is screened the same
way (same two detectors, symmetric) before being trusted; a flagged output is
treated exactly like an ungrounded one — retry, then fall back.
"""

from __future__ import annotations

import json

from flaskapp.travel_ai.agents.flight_agent.domain import (
    acknowledgment_is_valid,
    apply_acknowledgment,
    flight_preference_gaps,
    propose_flights,
    screen_flights,
)
from flaskapp.travel_ai.agents.flight_agent.guardrails import (
    screen_input_text,
    validate_grounded_explanation,
)
from flaskapp.travel_ai.agents.flight_agent.prompt import FLIGHT_AGENT_SYSTEM_PROMPT
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightAgentResponse,
    FlightInventoryItem,
    FlightProposal,
    FlightProposalRequest,
)

# The fail-closed model call itself lives in `structured_call.py`, shared with
# the tool loop so the retry-and-fall-back policy exists once. Re-exported here
# because this is where callers have always found these names.
from flaskapp.travel_ai.agents.flight_agent.structured_call import (  # noqa: F401
    FALLBACK_RATIONALE,
    MAX_ATTEMPTS,
    ChatModel,
    StructuredLLM,
    blocked_input_response as _blocked_input_response,
    fallback_response as _fallback_response,
    invoke_structured,
)


def _compact(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _build_messages(
    request: FlightProposalRequest, proposal: FlightProposal, screening: list, gaps: dict[str, list[str]]
) -> list[dict]:
    # Plain dicts, not langchain message objects — keeps this module import-
    # free of langchain, so tests don't need it installed to exercise the
    # grounding/fallback logic. The real ChatModel implementation (wired in
    # service.py once it exists) is responsible for turning these into
    # SystemMessage/HumanMessage.
    return [
        {"role": "system", "content": FLIGHT_AGENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _compact(
                {
                    "trip_context": request.trip_context.model_dump(),
                    "constraints": request.constraints.model_dump() if request.constraints else None,
                    "negotiation_history": [h.model_dump() for h in request.negotiation_history],
                    "proposal": proposal.model_dump(),
                    "screening": [s.model_dump() for s in screening],
                    "acknowledgeable_preference_gaps": gaps,
                }
            ),
        },
    ]


def _reason_over_proposal(
    request: FlightProposalRequest,
    proposal: FlightProposal,
    screening: list,
    gaps: dict[str, list[str]],
    llm: ChatModel,
    tracer,
) -> FlightAgentResponse:
    """One grounded model pass, isolated so option B can call it a second time
    (post-acknowledgment) without duplicating the logic.

    Grounding is membership in *this* proposal: the single-shot path searched
    once, so a flight outside `proposal.candidates` came from nowhere. The tool
    loop widens that set to everything it ever saw — see `structured_call.py`.
    """
    structured_llm = llm.with_structured_output(FlightAgentResponse, method="json_schema")
    return invoke_structured(
        structured_llm,
        _build_messages(request, proposal, screening, gaps),
        ground=lambda response: validate_grounded_explanation(
            response.highlighted_flight_ids, proposal
        ),
        proposal=proposal,
        tracer=tracer,
    )


def run_flight_agent(
    request: FlightProposalRequest,
    inventory: list[FlightInventoryItem],
    llm: ChatModel,
    *,
    tracer=None,
) -> tuple[FlightProposal, FlightAgentResponse]:
    """The tool always runs; the brain reasons over its output, fail-closed.

    Option B: if the brain proposes acknowledging one of its own soft
    preferences as unmet and `domain.acknowledgment_is_valid()` confirms a
    real, unanimous gap exists, this runs the tool once more with the
    acknowledgment applied and reasons again over the new result — at most
    one extra tool call, one extra LLM pass, ever. Anything else (a
    hallucinated or invalid acknowledgment) is ignored, not applied — the
    original proposal/response stands. Re-running never changes the
    candidates for this exact gap (everything in it already ties on the
    acknowledged criterion); it exists so the second pass's rationale can
    honestly say the wish was acknowledged rather than silently dropped.

    `tracer` is optional and duck-typed to `tracing.AuditTracer` (`.record(event,
    agent, details)`) — not required, so this stays testable without one.
    """
    proposal = propose_flights(request, inventory)
    screening = screen_flights(request, inventory)
    gaps = flight_preference_gaps(request, inventory)

    if tracer is not None:
        tracer.record("agent_started", "flight_agent", {"candidate_count": len(proposal.candidates)})

    input_screen = screen_input_text(
        request.trip_context.preferences, request.trip_context.refinement_notes
    )
    if input_screen["blocked"]:
        if tracer is not None:
            tracer.record("agent_input_blocked", "flight_agent", {
                "injection_count": len(input_screen["injection"]),
                "high_bias_count": len(input_screen["high_bias"]),
                "toxicity_count": len(input_screen["toxicity"]),
            })
        response = _blocked_input_response(proposal, input_screen)
        if tracer is not None:
            tracer.record("agent_completed", "flight_agent", {
                "escalate": response.escalate, "confidence": response.confidence,
                "acknowledgment_applied": False,
            })
        return proposal, response

    response = _reason_over_proposal(request, proposal, screening, gaps, llm, tracer)

    acknowledgment = response.proposed_acknowledgment
    if acknowledgment is not None and acknowledgment_is_valid(acknowledgment, gaps):
        if tracer is not None:
            tracer.record("agent_acknowledgment_applied", "flight_agent", {
                "field": acknowledgment.field, "direction": acknowledgment.direction,
                "reason": acknowledgment.reason,
            })
        updated_prefs = apply_acknowledgment(request.trip_context.flight_preferences, acknowledgment)
        updated_context = request.trip_context.model_copy(update={"flight_preferences": updated_prefs})
        updated_request = request.model_copy(update={"trip_context": updated_context})

        proposal = propose_flights(updated_request, inventory)  # the one extra tool call
        screening = screen_flights(updated_request, inventory)
        gaps = flight_preference_gaps(updated_request, inventory)
        response = _reason_over_proposal(updated_request, proposal, screening, gaps, llm, tracer)
        response = response.model_copy(update={"acknowledgment_applied": acknowledgment})
    elif acknowledgment is not None and tracer is not None:
        tracer.record("agent_acknowledgment_rejected", "flight_agent", {
            "field": acknowledgment.field, "direction": acknowledgment.direction,
            "reason": "no matching gap found; ignored, not applied",
        })

    if tracer is not None:
        tracer.record("agent_completed", "flight_agent", {
            "escalate": response.escalate, "confidence": response.confidence,
            "acknowledgment_applied": response.acknowledgment_applied is not None,
        })
    return proposal, response
