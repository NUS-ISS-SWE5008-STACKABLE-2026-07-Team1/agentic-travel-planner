"""Hotel & Transport Agent's LangGraph node — the grounded path.

This node does NOT ask a model to think of hotels. `domain.py` searches,
filters and ranks real inventory in code first; the model is then given that
finished proposal and asked only to explain it, with `guardrails` rejecting any
hotel_id it invents. Removing the model degrades the answer's prose, not its
correctness — the candidate list is identical either way.

Flow:

    A2A request -> adapter.to_hotel_request  (shared contract -> hotel contract)
                -> provider.fetch             (deterministic inventory)
                -> domain.propose_hotels       (deterministic, always runs)
                -> reasoning.run_hotel_agent   (model explains; grounding enforced)
                -> AgentFinding               (back onto the shared contract)

Inventory comes from an `HotelInventoryProvider` (see `providers/`), not from a
module-level dataset. Seed is the default. The node's logic is identical either
way — the only source-dependent things are the assumption text and whether
accessibility is verified or unknown.

Fallback: when the city or dates fall outside the loaded inventory, there is
nothing to ground an answer in. Rather than return an empty finding that reads
as a bug, the node falls back to the prompt-only specialist and attaches a
warning saying the options are model estimates rather than inventory-backed.
The orchestrator and traveller both see that distinction.

The node never raises on a planning shortfall. "No hotels for this city" is
an answer the orchestrator can act on; an exception is not.
"""

from __future__ import annotations

import time
from typing import Any

from flaskapp.config import Config, get_llm_settings
from flaskapp.database import save_agent_run, save_hotel_transport_eval_run
from flaskapp.travel_ai.a2a import response_message
from flaskapp.travel_ai.agents.base import make_specialist_node
from flaskapp.travel_ai.agents.hotel_transport_agent.adapter import to_hotel_request
from flaskapp.travel_ai.agents.hotel_transport_agent.domain import screen_hotels, transport_units
from flaskapp.travel_ai.agents.hotel_transport_agent.guardrails import (
    validate_grounded_explanation,
)
from flaskapp.travel_ai.agents.hotel_transport_agent.prompt import INSTRUCTION, PATH2_INSTRUCTION
from flaskapp.travel_ai.agents.hotel_transport_agent.providers import (
    get_hotel_inventory_provider,
)
from flaskapp.travel_ai.agents.hotel_transport_agent.providers.seed import (
    INVENTORY_ASSUMPTION,
)
from flaskapp.travel_ai.agents.hotel_transport_agent.reasoning import StructuredLLM, run_hotel_agent
from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import (
    HotelCandidate,
    HotelProposal,
    TransportOption,
)
from flaskapp.travel_ai.agents.hotel_transport_agent.seed_data import (
    transport_for,
    covers_transport,
)
from flaskapp.travel_ai.guardrails.specialist import make_postprocess, make_preflight
from flaskapp.travel_ai.schemas import AgentFinding, Option, TravelRequest
from flaskapp.travel_ai.terminal import log_payload
from flaskapp.travel_ai.usage import TokenUsageCallback

NAME = "hotel_transport_agent"

__all__ = ["NAME", "INVENTORY_ASSUMPTION", "ESTIMATE_WARNING",
           "NO_CONCRETE_OPTIONS_WARNING", "create_node"]

ESTIMATE_WARNING = (
    "These hotel and transport options are model estimates, not drawn from verified inventory. "
    "Confirm every detail with the property or carrier before booking."
)

# Paired with ESTIMATE_WARNING, not a replacement for it: that string carries the
# substring `safeguards.UNVERIFIED_OPTIONS_MARKER` matches on, which is what makes
# `enforce_provenance_disclosure` write the flag into the plan's own limitations.
NO_CONCRETE_OPTIONS_WARNING = (
    "No verified inventory covers this destination, so no specific properties or "
    "services are listed. The guidance above is general area knowledge, not live "
    "availability — confirm actual options, rates and accessibility with the property "
    "or operator."
)


def _forbid_concrete_options(agent_name: str, tracer):
    """Path 2 postprocess: screen the prose, then strip every concrete option.

    Deliberately identical in shape to `flight_agent/agent.py`'s version — see
    `docs/flight_agent/design.md` §3 for the full reasoning, which applies
    unchanged here: with no inventory to ground against, a model asked for
    options invents property names and nightly rates, and there is no candidate
    set for a grounding check to test membership against.

    Order matters. The generic L1 output screen runs FIRST, so prose that fails
    screening is replaced wholesale (by a canned finding carrying no options)
    rather than being stripped and kept.
    """
    screen_output = make_postprocess(agent_name)

    def postprocess(finding: AgentFinding, context=None) -> AgentFinding:
        finding = screen_output(finding, context)
        if not finding.options:
            return finding
        # A count, never the content.
        tracer.record("agent_path2_options_stripped", agent_name, {"count": len(finding.options)})
        return finding.model_copy(update={
            "options": [],
            "warnings": [*finding.warnings, NO_CONCRETE_OPTIONS_WARNING],
        })

    return postprocess


class _EvalSignals:
    """Reconstructs guardrail pass/block results for `hotel_transport_agent_eval_runs`
    purely by watching trace events `reasoning.py` already emits
    (`agent_input_blocked`, `agent_llm_attempt_failed`) — no change to
    `reasoning.py`'s own logic or return shape. Best-effort: 'pass' unless a
    specific violation event was actually observed.

    `guardrail_output_result` collapses to only 'pass'/'high': `screen_output_text`
    only ever flags on high-risk bias or flagged toxicity (medium bias never
    retries), so a 'medium' output-side classification can't happen here.
    """

    def __init__(self) -> None:
        self.input_blocked = False
        self.grounding_failed = False
        self.output_flagged = False
        self.block_reasons: list[str] = []

    def observe(self, event: str, details: dict | None) -> None:
        details = details or {}
        if event == "agent_input_blocked":
            self.input_blocked = True
            self.block_reasons.append("input screening blocked")
        elif event == "agent_llm_attempt_failed":
            if "ungrounded_hotel_ids" in details:
                self.grounding_failed = True
                self.block_reasons.append("ungrounded hotel id(s) in rationale")
            if "output_policy_violation" in details:
                self.output_flagged = True
                self.block_reasons.append("output policy violation")

    def as_row(self) -> dict[str, str | None]:
        return {
            "guardrail_input_result": "blocked" if self.input_blocked else "pass",
            "guardrail_output_result": "high" if self.output_flagged else "pass",
            "guardrail_grounding_result": "fail" if self.grounding_failed else "pass",
            "guardrail_block_reason": "; ".join(dict.fromkeys(self.block_reasons)) or None,
        }


class _InnerTracer:
    """Forwards `run_hotel_agent`'s events except the lifecycle duplicates,
    and — if given an `_EvalSignals` — feeds every event to it first, so eval
    logging observes exactly what the traveller-facing trace saw."""

    def __init__(self, tracer, signals: _EvalSignals | None = None):
        self._tracer = tracer
        self._signals = signals

    def record(self, event: str, agent: str, details: dict | None = None) -> None:
        if self._signals is not None:
            self._signals.observe(event, details)
        if event not in {"agent_started", "agent_completed"}:
            self._tracer.record(event, agent, details)


class _EvalRun:
    """One row of `hotel_transport_agent_eval_runs`, assembled as the request unfolds.

    Mirrors `flight_agent/agent.py`'s `_EvalRun` in shape: the row is built in one
    place and the node only says which ending happened. Best-effort telemetry —
    any exception in `_write` is swallowed and recorded as a trace event rather
    than turning an already-served request into a second failure.
    """

    def __init__(
        self, tracer, request_id: str, *, mode: str, inventory_source: str,
        llm_provider: str | None, llm_model: str | None,
    ) -> None:
        self._tracer = tracer
        self._request_id = request_id
        self._mode = mode
        self._inventory_source = inventory_source
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._started = time.perf_counter()
        self.signals = _EvalSignals()

    def _write(self, **fields) -> None:
        if not self._tracer.database_path:
            return
        try:
            save_hotel_transport_eval_run(
                self._tracer.database_path,
                run_type="production",
                request_id=self._request_id,
                hotel_agent_mode=self._mode,
                inventory_source=self._inventory_source,
                llm_provider=self._llm_provider,
                llm_model=self._llm_model,
                latency_ms=int((time.perf_counter() - self._started) * 1000),
                **self.signals.as_row(),
                **fields,
            )
        except Exception as exc:  # noqa: BLE001 - telemetry must never break the response
            self._tracer.record("agent_eval_log_failed", NAME, {"error_type": type(exc).__name__})

    def path2_fallback(self, *, options_returned: int) -> None:
        """No inventory covered the trip, so the prompt-only path answered."""
        self._write(outcome="path2_fallback", options_returned=options_returned)

    def success(
        self, *, counts: dict, usage: dict, options_returned: int,
        hotel_candidate_count: int, transport_option_count: int,
    ) -> None:
        """A finding was produced."""
        self._write(
            outcome="success",
            llm_turns=counts.get("llm_turns", 0),
            tool_calls=counts.get("tool_calls", 0),
            provider_calls=counts.get("provider_calls", 0),
            usage=usage,
            options_returned=options_returned,
            hotel_candidate_count=hotel_candidate_count,
            transport_option_count=transport_option_count,
        )

    def error(self, exc: BaseException, *, usage: dict) -> None:
        self._write(
            outcome="error", error_type=type(exc).__name__,
            usage=usage,
        )


def _hotel_candidate_to_option(
    candidate: HotelCandidate, assumption: str
) -> Option:
    """One ranked candidate as a shared-contract `Option`."""
    factors = [
        f"{candidate.star_rating or 'N/A'} star rating" if candidate.star_rating else "Unrated",
        f"Price per night: {candidate.price_per_night:.2f} {candidate.currency or 'SGD'}",
        f"Total estimated: {candidate.estimated_total_cost:.2f} {candidate.currency or 'SGD'}" if candidate.estimated_total_cost else "",
    ]
    if candidate.distance_to_center_km is not None:
        factors.append(f"{candidate.distance_to_center_km:.1f} km from centre")
    if candidate.wheelchair_accessible:
        factors.append("Wheelchair accessible")
    if candidate.step_free_entrance:
        factors.append("Step-free entrance")
    if candidate.accessible_bathroom:
        factors.append("Accessible bathroom")
    if candidate.amenities:
        factors.append(f"Amenities: {', '.join(candidate.amenities[:5])}")

    return Option(
        category="hotel",
        name=candidate.name,
        description=(
            f"{candidate.room_type} room at {candidate.name}, "
            + (f"{candidate.distance_to_center_km:.1f} km from centre. " if candidate.distance_to_center_km is not None else "")
            + f"{candidate.star_rating or 'N/A'} star rating."
        ),
        estimated_cost=candidate.estimated_total_cost,
        currency=candidate.currency or "SGD",
        assumptions=[assumption],
        selection_factors=[f for f in factors if f],
    )


def transport_options_for_city(
    destination: str, destination_city: str | None, currency: str,
    arrival_airport: str | None = None,
) -> list[Option]:
    """Transport for every airport the destination city has.

    Resolved from the city rather than from the flight agent's findings. Those
    findings are empty when this agent runs — both start from START — which is
    why `transport_option_count` was 0 on every grounded run while 46 seed
    options sat unused.

    Fetching for ALL of the city's airports also avoids a question staging could
    not answer: one flight finding spans several arrival airports, so choosing
    one would pair a Haneda arrival with the Narita Express. Each option carries
    the airport it serves, and the package picks the matching one.

    `arrival_airport` — when known from the selected flight — is prioritised
    first in the list so the most relevant transfer surfaces at the top. It
    does not exclude other airports: a traveller may still want a cheaper
    option from a different gateway.
    """
    from flaskapp.places import find_city

    if not destination_city:
        return []
    city = find_city(destination, destination_city)
    if city is None:
        return []
    options: list[Option] = []
    for transport in transport_for_city(
        destination, destination_city, arrival_airport
    ):
        options.append(_transport_to_option(transport, currency))
    return options


def transport_for_city(
    destination: str, destination_city: str | None,
    arrival_airport: str | None = None,
):
    """Every seeded transfer for the destination city, tagged with its airport.

    Returns transport for all of the city's airports. When `arrival_airport`
    is provided (from the flight agent's selected flight), options for that
    airport are listed first so the most relevant transfers surface early.
    This avoids the bug where the arrival airport was resolved but never
    used — transport was fetched for all airports with no priority signal.
    """
    from flaskapp.places import find_city

    if not destination_city:
        return []
    city = find_city(destination, destination_city)
    if city is None:
        return []

    def _key(airport: str) -> int:
        if arrival_airport and airport == arrival_airport:
            return 0
        return 1

    airports = sorted(city.airports, key=_key)
    return [
        transport.model_copy(update={"airport": airport})
        for airport in airports
        for transport in transport_for(city.slug, airport)
    ]


def _transport_to_option(
    option: TransportOption, currency: str, travellers: int = 1
) -> Option:
    """Transport option as a shared-contract Option.

    The seed cost is for ONE unit — one car, or one ticket — so it is scaled by
    what the party actually needs before it reaches the traveller or
    `recommend_package`. See `domain.transport_units` for which mode is which.
    """
    units = transport_units(option.mode, travellers)
    scaled_cost = (
        round(option.estimated_cost * units, 2)
        if option.estimated_cost is not None else None
    )
    desc_parts = [f"{option.mode} — {option.name}"]
    if option.frequency:
        desc_parts.append(f"{option.frequency}")
    if option.duration_minutes:
        desc_parts.append(f"{option.duration_minutes} min")
    if option.distance_km:
        desc_parts.append(f"{option.distance_km:.1f} km")
    # The cost is in the option's own currency (SGD from seed data);
    # use that rather than the request currency so the figure matches
    # the label.
    cost_currency = option.currency or currency
    if scaled_cost:
        # Say what the multiplier was, so a traveller can check the figure
        # rather than wonder why a 12.00 train ticket reads as 60.00.
        unit_note = (
            "" if units == 1
            else f" ({units} x {option.estimated_cost:.2f})"
        )
        desc_parts.append(f"~{scaled_cost:.2f} {cost_currency}{unit_note}")

    return Option(
        category="transport",
        airport=option.airport,
        name=option.name,
        description=", ".join(desc_parts),
        estimated_cost=scaled_cost,
        currency=cost_currency,
        assumptions=option.assumptions,
        limitations=option.limitations,
        selection_factors=option.accessibility_notes or [],
    )


def create_node(llm, tracer, provider=None, config=None):
    """The graph node. Grounded when inventory covers the city, prompt-only otherwise."""
    # Both hooks have existed on `make_specialist_node` since it was written and
    # this call site had never used either, so the fallback path ran with no input
    # and no output screening. Fixed alongside the flight agent deliberately: these
    # two modules are near-duplicates, and `docs/progress.md` records that the last
    # guardrail fix had to be applied twice because one of them was missed.
    prompt_only_node = make_specialist_node(
        NAME, PATH2_INSTRUCTION, llm, tracer,
        preflight=make_preflight(NAME),
        postprocess=_forbid_concrete_options(NAME, tracer),
    )
    provider_note = None
    if provider is None:
        provider, provider_note = get_hotel_inventory_provider(
            vars(Config) if config is None else config
        )
    assumption = getattr(provider, "assumption", INVENTORY_ASSUMPTION)
    # Resolved once here, same as the flight agent: eval logging records what
    # actually ran, not what the current settings say, so a config change
    # mid-deployment doesn't retroactively relabel earlier rows.
    _llm_settings, _llm_settings_error = get_llm_settings(vars(Config) if config is None else config)
    llm_provider_name = (_llm_settings or {}).get("provider")
    llm_model_name = (_llm_settings or {}).get("model")

    def hotel_node(state) -> dict:
        # First statement in the node: the recorder starts its clock here, and
        # `latency_ms` is meant to cover everything the node does.
        eval_run = _EvalRun(
            tracer, state["request_id"], mode="grounded",
            inventory_source=provider.name,
            llm_provider=llm_provider_name, llm_model=llm_model_name,
        )
        incoming = next(
            (m for m in state.get("messages", [])
             if m.message_type == "request" and m.recipient == NAME),
            None,
        )
        if incoming is None:
            raise ValueError(f"Missing A2A request for {NAME}")

        travel_request = TravelRequest.model_validate(state["request"])
        adapted = to_hotel_request(
            travel_request,
            flight_candidates=[
                opt.model_dump()
                for finding in state.get("findings", [])
                if finding.agent == "flight_agent"
                for opt in finding.options
            ],
        )
        notes = [*adapted.unresolved, *([provider_note] if provider_note else [])]

        # One fetch per request, reused by both the proposal and the screening
        # trace below.
        hotel_inventory: list[Any] = []
        transport_options: list[TransportOption] = []
        if provider.covers(adapted.request):
            result = provider.fetch(adapted.request)
            hotel_inventory = result.items
            notes.extend(result.notes)

        # Transport seed data is static — no provider abstraction yet.
        ctx = adapted.request.trip_context
        # Resolved from the destination CITY, not from the flight agent's
        # findings: those are empty here because both agents start from START,
        # which is why this lookup never once returned anything in production.
        # arrival_airport is passed to prioritise transport for the selected
        # flight's airport — previously resolved but never consumed.
        transport_options = transport_for_city(
            travel_request.destination, travel_request.destination_city,
            ctx.arrival_airport,
        )

        from flaskapp.places import find_city

        transport_covered = bool(transport_options)
        city_resolved = None
        if travel_request.destination_city:
            city_resolved = find_city(travel_request.destination, travel_request.destination_city)
        if city_resolved:
            if ctx.arrival_airport:
                transport_covered = covers_transport(city_resolved.slug, ctx.arrival_airport)
            else:
                transport_covered = any(
                    covers_transport(city_resolved.slug, ap) for ap in city_resolved.airports
                )
        else:
            transport_covered = bool(transport_options)

        if not transport_options:
            notes.append(
                "No verified transport options are available for the selected arrival airport."
            )

        # All seed data is denominated in SGD. If the traveller's currency
        # differs, flag it so they know prices are in SGD, not their
        # chosen currency.
        if travel_request.currency != "SGD":
            notes.append(
                f"All prices are in SGD; your request currency is "
                f"{travel_request.currency}. Confirm conversion rates before booking."
            )

        if not hotel_inventory and not transport_options:
            tracer.record("agent_fallback", NAME, {
                "reason": "no inventory for city/transport",
                "source": provider.name,
                "transport_covered": transport_covered,
                "arrival_airport": ctx.arrival_airport,
                "unresolved": notes,
            })
            result = prompt_only_node(state)
            for finding in result.get("findings", []):
                finding.warnings = [*finding.warnings, *notes, ESTIMATE_WARNING]
            eval_run.path2_fallback(
                options_returned=sum(len(f.options) for f in result.get("findings", [])),
            )
            return result

        tracer.record("agent_started", NAME, {"mode": "grounded", "source": provider.name})
        if tracer.database_path:
            save_agent_run(tracer.database_path, state["request_id"], NAME, "processing")

        usage = TokenUsageCallback()
        try:
            proposal, ranked_transport, response = run_hotel_agent(
                adapted.request, hotel_inventory, transport_options,
                StructuredLLM(llm, callbacks=[usage]),
                tracer=_InnerTracer(tracer, eval_run.signals),
            )
            screening = screen_hotels(adapted.request, hotel_inventory, ctx.dest_city_slug)

            options = [
                _hotel_candidate_to_option(c, assumption)
                for c in proposal.candidates
            ] + [
                _transport_to_option(t, travel_request.currency, travel_request.travellers)
                for t in ranked_transport
            ]

            warnings = list(notes)
            if response.escalate and response.escalation_reason:
                warnings.append(f"Escalation requested: {response.escalation_reason}")

            finding = AgentFinding(
                agent=NAME,
                summary=response.rationale,
                options=options,
                warnings=warnings,
                confidence=response.confidence,
            )
            log_payload(f"REQUEST {state['request_id']} | {NAME.upper()} RESPONSE", finding)
            tracer.record("agent_completed", NAME, {
                "mode": "grounded",
                "source": provider.name,
                "confidence": finding.confidence,
                "option_count": len(finding.options),
                "warning_count": len(finding.warnings),
                "hotel_candidate_count": len(proposal.candidates),
                "transport_option_count": len(ranked_transport),
                "screened_count": len(screening),
                "excluded_count": sum(1 for s in screening if not s.included),
                **usage.as_dict(),
            })
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path, state["request_id"], NAME, "completed",
                    usage.as_dict(), finding,
                )
            eval_run.success(
                counts={},
                usage=usage.as_dict(),
                options_returned=len(finding.options),
                hotel_candidate_count=len(proposal.candidates),
                transport_option_count=len(ranked_transport),
            )
            outgoing = response_message(
                request=incoming, sender=NAME, payload_type="AgentFinding", payload=finding
            )
            return {"findings": [finding], "messages": [outgoing]}
        except Exception as exc:
            tracer.record("agent_failed", NAME, {"error_type": type(exc).__name__})
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path, state["request_id"], NAME, "failed",
                    usage.as_dict(), error_type=type(exc).__name__,
                )
            eval_run.error(exc, usage=usage.as_dict())
            raise

    return hotel_node
