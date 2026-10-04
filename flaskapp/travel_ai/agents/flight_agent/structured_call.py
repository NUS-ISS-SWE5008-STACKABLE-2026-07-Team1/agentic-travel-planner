"""The fail-closed model call, once, for both of this agent's reasoning paths.

`reasoning.py` (single-shot) and `agentic.py` (tool loop) both end the same way:
ask the model for a `FlightAgentResponse`, check the flight IDs it cited are real
and its prose clears output screening, retry once if either fails, and fall back
to a narrative-free response rather than trust ungrounded output.

That algorithm used to exist twice, and `agentic.py` imported three private names
out of `reasoning.py` to avoid a third copy. This is the gate between a
hallucinated flight number and the traveller, so a fix landing in one copy and
missing the other is a safety regression rather than untidiness — it lives here
now, and each path supplies only what genuinely differs:

| | single-shot | tool loop |
|---|---|---|
| messages | proposal + screening + gaps | the whole loop transcript |
| grounding check | membership in `proposal.candidates` | membership in `cache.seen_ids` |

The second row is the reason this takes a `ground` callable rather than a flag.
The loop's set is legitimately wider: a flight found in an earlier search but
ranked out of the final proposal is still discussable, while anything outside
everything a provider ever returned is a fabrication either way.

**Langchain-free, like its two callers.** `messages` is passed straight through
to the model and never inspected, so the same function serves plain dicts from
`reasoning.py` and `langchain_core` message objects from `agentic.py`.
`tests/test_flight_imports.py` pins that property.

**Not yet generalised past this agent, deliberately.** `hotel_transport_agent`,
`risk_advisory_agent` and `orchestrator_agent` each carry their own
`_reason_over_proposal`/`_fallback_response`/`MAX_ATTEMPTS` of the same shape.
Folding those in is a real improvement and a real cross-owner change (CLAUDE.md
gives each agent a folder and an owner), so it is recorded as a follow-up rather
than taken unilaterally here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from flaskapp.travel_ai.agents.flight_agent.guardrails import screen_output_text
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightAgentResponse,
    FlightProposal,
)

AGENT = "flight_agent"

FALLBACK_RATIONALE = (
    "Automated explanation unavailable this round; showing ranked candidates "
    "without narrative rationale. All listed options are still fully grounded "
    "in real inventory."
)

# Two, not more: the retry exists to absorb a single bad sample, not to argue a
# model into compliance. `agentic.py` applies it to the terminal turn only, so
# the loop's worst case stays `max_llm_turns + MAX_ATTEMPTS` model calls.
MAX_ATTEMPTS = 2


class StructuredLLM(Protocol):
    """The narrow shape needed from a LangChain chat model after
    `.with_structured_output(FlightAgentResponse, ...)` — lets tests inject a
    stub without importing langchain_openai at all."""

    def invoke(self, messages: list[Any], config: dict | None = None) -> FlightAgentResponse: ...


class ChatModel(Protocol):
    def with_structured_output(self, schema: type, method: str) -> StructuredLLM: ...


def fallback_response(proposal: FlightProposal) -> FlightAgentResponse:
    """What the traveller gets when the model cannot be trusted this round.

    Still every ranked candidate, because the deterministic proposal never
    depended on the model being available — only the prose does.
    """
    return FlightAgentResponse(
        rationale=FALLBACK_RATIONALE,
        highlighted_flight_ids=[c.flight_id for c in proposal.candidates],
        confidence=0.0,
    )


def blocked_input_response(
    proposal: FlightProposal, screen_result: dict
) -> FlightAgentResponse:
    """What the traveller gets when their own input never reached the model.

    Escalates, because a blocked request is a human-review case rather than a
    planning shortfall, and names the category without echoing the text.
    """
    reasons = []
    if screen_result["injection"]:
        reasons.append("instruction-like content")
    if screen_result["high_bias"]:
        reasons.append("high-risk biased/stereotyping content")
    if screen_result["toxicity"]:
        reasons.append("toxic content")
    reason_text = ", ".join(reasons) or "an input policy violation"
    return FlightAgentResponse(
        rationale=(
            "Traveller-stated preferences were not sent to the reasoning model because "
            f"input screening flagged {reason_text}. Showing ranked candidates without "
            "narrative rationale; a human should review the original request."
        ),
        highlighted_flight_ids=[c.flight_id for c in proposal.candidates],
        escalate=True,
        escalation_reason=f"Input screening blocked this request: {reason_text}.",
        confidence=0.0,
    )


def invoke_structured(
    structured_llm: StructuredLLM,
    messages: list[Any],
    *,
    ground: Callable[[FlightAgentResponse], list[str]],
    proposal: FlightProposal,
    tracer=None,
    agent: str = AGENT,
    callbacks: list | None = None,
) -> FlightAgentResponse:
    """Ask the model, verify, retry once, then fall back. Never raises.

    `callbacks` reach every attempt, including a rejected one: a sample that
    fails grounding was still paid for, so the node's token count includes it.

    `ground` returns the flight IDs the response cited that it had no business
    citing — empty means grounded. Both callers pass a check over a set they
    own, which is what keeps this function ignorant of where rows came from.

    Three failures are treated identically on purpose: an exception from the
    provider, an ungrounded flight ID, and prose that fails output screening.
    All three mean "this sample is not usable", and the response either clears
    every gate or does not leave here at all.
    """
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = structured_llm.invoke(messages, config={"callbacks": callbacks or []})
        except Exception as exc:  # noqa: BLE001 - any model failure falls back
            if tracer is not None:
                tracer.record("agent_llm_attempt_failed", agent, {
                    "attempt": attempt, "error_type": type(exc).__name__,
                })
            continue

        ungrounded = ground(response)
        screened = screen_output_text(response.rationale)
        if not ungrounded and not screened["flagged"]:
            return response

        if tracer is not None:
            details: dict[str, Any] = {"attempt": attempt}
            if ungrounded:
                details["ungrounded_flight_ids"] = ungrounded
            if screened["flagged"]:
                details["output_policy_violation"] = {
                    "bias": screened["bias"], "toxicity": screened["toxicity"],
                }
            tracer.record("agent_llm_attempt_failed", agent, details)

    return fallback_response(proposal)
