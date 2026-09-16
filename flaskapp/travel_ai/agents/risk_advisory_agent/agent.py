"""Risk & Advisory Agent's LangGraph node — the grounded path.

This node does NOT ask a model to think of risks. `domain.py` queries the
reference tables in code first; the model is then given that finished list
and asked only to prioritise, connect, and narrate it, with `guardrails`
rejecting any risk_id it invents. Removing the model degrades the wording,
not the underlying facts.

Flow:

    A2A request -> adapter.to_risk_request      shared contract -> risk contract
                -> provider.covers(request)?
                     no  -> prompt-only fallback (own guardrails, see below)
                     yes -> domain.propose_risks   deterministic, always runs
                            -> reasoning.run_risk_agent   model narrates; grounding enforced
                -> AgentFinding                    back onto the shared contract

Reference data comes from a `RiskDataProvider` (see `providers/`) rather than
being read here directly — today that is `SeedRiskProvider`, reading the
CSV-loaded content in `seed_data.py`; a future live-retrieval provider would
plug in behind the same interface with no change to this file.

Fallback: when the destination has no reference data (an unmapped city, or
one outside the five the team seeded), there is nothing to ground an answer
in, so the node falls back to the prompt-only path — screened by this
agent's OWN guardrails, not borrowed from another agent's, from the first
version of this file rather than as later debt — with a disclosure attached
so the estimate is never mistaken for reference-data-backed output.
"""

from __future__ import annotations

from flaskapp.config import Config
from flaskapp.database import save_agent_run
from flaskapp.travel_ai.a2a import response_message
from flaskapp.travel_ai.agents.base import make_specialist_node
from flaskapp.travel_ai.agents.risk_advisory_agent.adapter import to_risk_request
from flaskapp.travel_ai.agents.risk_advisory_agent.domain import propose_risks
from flaskapp.travel_ai.agents.risk_advisory_agent.guardrails import screen_input, screen_output
from flaskapp.travel_ai.agents.risk_advisory_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.agents.risk_advisory_agent.providers import get_risk_data_provider
from flaskapp.travel_ai.agents.risk_advisory_agent.reasoning import run_risk_agent
from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskItem, RiskProposal
from flaskapp.travel_ai.guardrails.fields import collect_free_text
from flaskapp.travel_ai.schemas import AgentFinding, Option, TravelRequest
from flaskapp.travel_ai.terminal import log_payload
from flaskapp.travel_ai.usage import TokenUsageCallback

NAME = "risk_advisory_agent"

__all__ = ["NAME", "ESTIMATE_WARNING", "create_node"]

ESTIMATE_WARNING = (
    "These risk notes are model estimates, not drawn from verified reference data. "
    "Confirm every detail with official travel advisories before departure."
)


class _InnerTracer:
    """Forwards `run_risk_agent`'s events except the lifecycle duplicates
    this module's own `tracer.record` calls already cover."""

    def __init__(self, tracer):
        self._tracer = tracer

    def record(self, event: str, agent: str, details: dict | None = None) -> None:
        if event not in {"agent_started", "agent_completed"}:
            self._tracer.record(event, agent, details)


def _risk_preflight(state):
    """Block the model call when the traveller's own free text fails this
    agent's own input screening. Deliberately not the shared `guardrails/
    specialist.py` wrapper — that reaches into `flight_agent`'s detectors,
    which is exactly the borrowed dependency this agent's guardrails exist to
    not have."""
    result = screen_input(collect_free_text(state.get("request", {})))
    if not result["blocked"]:
        return None
    reasons = []
    if result["injection"]:
        reasons.append("instruction-like content")
    if result["high_bias"]:
        reasons.append("high-risk generalising content")
    if result["toxicity"]:
        reasons.append("toxic content")
    return AgentFinding(
        agent=NAME,
        summary=f"{NAME} analysis was not sent to the model because input screening failed.",
        warnings=[f"Human review required: blocked {', '.join(reasons)}."],
        confidence=0.0,
    )


def _finding_text(finding: AgentFinding) -> str:
    values = [finding.summary, *finding.warnings]
    for option in finding.options:
        values.extend([option.name, option.description, *option.selection_factors])
    return "\n".join(values)


def _risk_postprocess(finding: AgentFinding, _context=None) -> AgentFinding:
    if screen_output(_finding_text(finding))["flagged"]:
        return AgentFinding(
            agent=NAME,
            summary=f"Generated {NAME} analysis was withheld by output guardrails.",
            warnings=["Human review required before acting on this section."],
            confidence=0.0,
        )
    return finding


def _item_to_option(item: RiskItem) -> Option:
    description = item.detail
    if item.mitigation:
        description = f"{description} Mitigation: {item.mitigation}"
    factors = [f"category: {item.category}"]
    if item.severity:
        factors.append(f"severity: {item.severity}")
    if item.applies_to:
        factors.append(f"applies to: {item.applies_to}")
    return Option(
        name=item.title, description=description,
        assumptions=[item.source], selection_factors=factors,
    )


def _build_finding(proposal: RiskProposal, response, notes: list[str]) -> AgentFinding:
    warnings = list(notes)
    if response.escalate:
        warnings.insert(0, f"ESCALATE: {response.escalation_reason or 'One or more high-severity risk items require review before planning continues.'}")
    return AgentFinding(
        agent=NAME,
        summary=response.rationale,
        options=[_item_to_option(item) for item in proposal.items],
        warnings=warnings,
        confidence=response.confidence,
    )


def create_node(llm, tracer, provider=None, config=None):
    """The graph node. Grounded when the destination has reference data,
    prompt-only otherwise."""
    prompt_only_node = make_specialist_node(
        NAME, INSTRUCTION, llm, tracer, preflight=_risk_preflight, postprocess=_risk_postprocess,
    )
    provider_note = None
    if provider is None:
        provider, provider_note = get_risk_data_provider(vars(Config) if config is None else config)

    def risk_node(state) -> dict:
        incoming = next(
            (m for m in state.get("messages", [])
             if m.message_type == "request" and m.recipient == NAME),
            None,
        )
        if incoming is None:
            raise ValueError(f"Missing A2A request for {NAME}")

        travel_request = TravelRequest.model_validate(state["request"])
        adapted = to_risk_request(travel_request)
        notes = [*adapted.unresolved, *([provider_note] if provider_note else [])]

        if not provider.covers(adapted.request):
            tracer.record("agent_fallback", NAME, {
                "reason": "no reference data for destination", "source": provider.name, "unresolved": notes,
            })
            result = prompt_only_node(state)
            for finding in result.get("findings", []):
                finding.warnings = [*finding.warnings, *notes, ESTIMATE_WARNING]
            return result

        tracer.record("agent_started", NAME, {"mode": "grounded", "source": provider.name})
        if tracer.database_path:
            save_agent_run(tracer.database_path, state["request_id"], NAME, "processing")

        usage = TokenUsageCallback()
        try:
            proposal = propose_risks(adapted.request, provider)
            response = run_risk_agent(
                adapted.request, proposal, llm, tracer=_InnerTracer(tracer),
            )
            finding = _build_finding(proposal, response, notes)
            log_payload(f"REQUEST {state['request_id']} | {NAME.upper()} RESPONSE", finding)
            tracer.record("agent_completed", NAME, {
                "mode": "grounded", "source": provider.name,
                "confidence": finding.confidence, "option_count": len(finding.options),
                "warning_count": len(finding.warnings), "item_count": len(proposal.items),
                **usage.as_dict(),
            })
            if tracer.database_path:
                save_agent_run(tracer.database_path, state["request_id"], NAME, "completed", usage.as_dict(), finding)
            outgoing = response_message(request=incoming, sender=NAME, payload_type="AgentFinding", payload=finding)
            return {"findings": [finding], "messages": [outgoing]}
        except Exception as exc:
            tracer.record("agent_failed", NAME, {"error_type": type(exc).__name__})
            if tracer.database_path:
                save_agent_run(tracer.database_path, state["request_id"], NAME, "failed", usage.as_dict(), error_type=type(exc).__name__)
            raise

    return risk_node
