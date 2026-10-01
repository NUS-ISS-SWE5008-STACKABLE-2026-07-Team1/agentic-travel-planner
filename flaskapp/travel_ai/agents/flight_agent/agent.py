"""Flight Agent's LangGraph node — the grounded path.

This node does NOT ask a model to think of flights. `domain.py` searches,
filters and ranks real inventory in code first; the model is then given that
finished proposal and asked only to explain it, with `guardrails` rejecting any
flight ID it invents. Removing the model degrades the answer's prose, not its
correctness — the candidate list is identical either way.

Flow:

    A2A request -> adapter.to_flight_request  (shared contract -> flight contract)
                -> ToolContext + one inventory fetch
                -> reasoners.select_reasoner  (structured | agentic, per mode)
                -> FlightReasoner.run         (search in code, model explains)
                -> AgentFinding               (back onto the shared contract)

The node owns the request's *lifecycle* — adapting it, fetching inventory once,
tracing, eval logging, building the finding. It does not own the reasoning: both
ways of thinking live in `reasoners.py` behind one interface, so this module
never branches on which one ran.

Inventory comes from an `InventoryProvider` (see `providers/`), not from a
module-level dataset. Seed is the default; `FLIGHT_INVENTORY_SOURCE=duffel`
swaps in a live supplier search. The node's logic is identical either way — the
only source-dependent things are the assumption text on each option and the
fact that a live feed can leave accessibility unverified.

Fallback: when the route or dates fall outside the loaded inventory, there is
nothing to ground an answer in. Rather than return an empty finding that reads
as a bug, the node falls back to the prompt-only specialist every other agent
uses, and attaches a warning saying the options are model estimates rather than
inventory-backed. `assess_plan` and the traveller both see that distinction. A
live supplier that errors or returns nothing lands in exactly the same branch.

The node never raises on a planning shortfall. "No flights for this route" is
an answer the orchestrator can act on; an exception is not.
"""

from __future__ import annotations

import time

from flaskapp.config import Config, get_llm_settings
from flaskapp.database import save_agent_run, save_flight_eval_run
from flaskapp.travel_ai.a2a import response_message
from flaskapp.travel_ai.agents.base import make_specialist_node
from flaskapp.travel_ai.agents.flight_agent.adapter import to_flight_request
from flaskapp.travel_ai.agents.flight_agent.domain import _party_size
from flaskapp.travel_ai.agents.flight_agent.prompt import INSTRUCTION, PATH2_INSTRUCTION
from flaskapp.travel_ai.agents.flight_agent.providers import get_inventory_provider
from flaskapp.travel_ai.agents.flight_agent.providers.seed import INVENTORY_ASSUMPTION
from flaskapp.travel_ai.agents.flight_agent.reasoners import select_reasoner
from flaskapp.travel_ai.agents.flight_agent.tools import ToolContext, budget_from_config
from flaskapp.travel_ai.agents.loop import EVENT_BUDGET_EXHAUSTED
from flaskapp.travel_ai.guardrails.specialist import make_postprocess, make_preflight
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightCandidate, FlightProposal
from flaskapp.travel_ai.schemas import AgentFinding, Option, OptionSchedule, TravelRequest
from flaskapp.travel_ai.terminal import log_payload
from flaskapp.travel_ai.usage import TokenUsageCallback

NAME = "flight_agent"

# Re-exported from the seed provider, which owns the wording now that each
# provider states its own. Kept importable from here because that is where
# callers have always found it.
__all__ = ["NAME", "INVENTORY_ASSUMPTION", "ESTIMATE_WARNING", "create_node"]

UNVERIFIED_WHEELCHAIR = (
    "Wheelchair assistance could not be verified for this flight — the supplier "
    "feed does not publish it. Confirm directly with the carrier before booking."
)
UNVERIFIED_STEP_FREE = (
    "Step-free boarding could not be verified for this flight — the supplier "
    "feed does not publish it."
)

ESTIMATE_WARNING = (
    "These flight options are model estimates, not drawn from verified inventory. "
    "Confirm every detail with the carrier."
)

# Paired with ESTIMATE_WARNING, not a replacement for it: that string carries the
# substring `safeguards.UNVERIFIED_OPTIONS_MARKER` matches on, which is what makes
# `enforce_provenance_disclosure` write the flag into the plan's own limitations.
# Reword or drop ESTIMATE_WARNING and that disclosure silently stops firing.
NO_CONCRETE_OPTIONS_WARNING = (
    "No verified inventory covers this route, so no specific flights are listed. "
    "The guidance above is general route knowledge, not live availability — confirm "
    "actual flights, times and fares with a carrier or booking site."
)


# `run_flight_agent` emits its own agent_started/agent_completed because it is
# also usable standalone (the demo scripts call it directly, with no graph).
# Inside the node those two duplicate the lifecycle events this module already
# records, and the admin monitor reads that stream — so they are dropped here
# while everything genuinely new (acknowledgment applied/rejected, blocked input,
# failed LLM attempts) passes through untouched.
_DUPLICATE_EVENTS = frozenset({"agent_started", "agent_completed"})


class _EvalSignals:
    """Reconstructs guardrail pass/block results for `flight_agent_eval_runs`
    purely by watching trace events `reasoning.py`/`agentic.py` already emit
    (`agent_input_blocked`, `agent_llm_attempt_failed`) — no change to either
    module's own logic or return shape. Best-effort: 'pass' unless a specific
    violation event was actually observed.

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
            if "ungrounded_flight_ids" in details:
                self.grounding_failed = True
                self.block_reasons.append("ungrounded flight id(s) in rationale")
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
    """Forwards `run_flight_agent`'s events except the lifecycle duplicates,
    and — if given an `_EvalSignals` — feeds every event to it first, so eval
    logging observes exactly what the traveller-facing trace saw."""

    def __init__(self, tracer, signals: _EvalSignals | None = None):
        self._tracer = tracer
        self._signals = signals

    def record(self, event: str, agent: str, details: dict | None = None) -> None:
        if self._signals is not None:
            self._signals.observe(event, details)
        if event not in _DUPLICATE_EVENTS:
            self._tracer.record(event, agent, details)


def _forbid_concrete_options(agent_name: str, tracer):
    """Path 2 postprocess: screen the prose, then strip every concrete flight.

    `docs/flight_agent/design.md` §3 names this path as the design's weakest
    point. With no inventory to ground against, a model asked for flight options
    returns invented flight numbers, times and fares — and the guardrail aimed at
    exactly that (`validate_grounded_explanation`) cannot run here, because the
    candidate set it checks membership against does not exist. Disclosure was the
    only control, and a warning in `warnings` is weaker than a fabricated option
    in `options`, which is the part the UI renders most prominently.

    This is design.md §3's option 3: keep the guidance, drop the specifics. The
    prompt asks for the same thing, but a prompt is not a guarantee — this is.

    Order matters. The generic L1 output screen runs FIRST, so prose that fails
    bias/toxicity/injection screening is replaced wholesale (by a canned finding
    that carries no options) rather than being stripped and kept.
    """
    screen_output = make_postprocess(agent_name)

    def postprocess(finding: AgentFinding, context=None) -> AgentFinding:
        finding = screen_output(finding, context)
        if not finding.options:
            return finding
        # A count, never the content: the fabricated flight numbers are exactly
        # what `tracing.py` says must not enter the audit trail.
        tracer.record("agent_path2_options_stripped", agent_name, {"count": len(finding.options)})
        return finding.model_copy(update={
            "options": [],
            "warnings": [*finding.warnings, NO_CONCRETE_OPTIONS_WARNING],
        })

    return postprocess


def _accessibility_limitations(candidate: FlightCandidate) -> list[str]:
    """Three-way, because "we checked and it isn't offered" and "nobody told
    us" are different facts and only one of them is a property of the flight.

    Collapsing `None` into the "not offered" message would libel flights that
    do offer assistance; collapsing it into silence would let an unchecked
    flight read as checked. Both failures land on the traveller who can least
    afford to find out at the gate, so unknown gets its own wording.
    """
    limitations: list[str] = []
    if candidate.wheelchair_assist_available is False:
        limitations.append("Wheelchair assistance is not offered on this flight.")
    elif candidate.wheelchair_assist_available is None:
        limitations.append(UNVERIFIED_WHEELCHAIR)
    if candidate.step_free_boarding is None:
        limitations.append(UNVERIFIED_STEP_FREE)
    return limitations


def _candidate_to_option(
    candidate: FlightCandidate, currency: str, assumption: str, party_size: int = 1
) -> Option:
    """One ranked candidate as a shared-contract `Option`.

    `selection_factors` restates only what the deterministic ranking actually
    used, so the traveller-visible reasons match the code that produced the
    order rather than a model's account of it.

    `party_size` must be the SAME one `domain.rank_flights` used, because
    `estimated_cost` here has to equal `domain._effective_cost` there. If the
    two drift, the cheapest option by the ranking stops being the cheapest
    option on screen, and nothing in the UI would reveal it.
    """
    factors = [
        "Direct" if candidate.stops == 0 else f"{candidate.stops} stop(s)",
        f"Arrives {candidate.arr_ts}",
        f"{candidate.seats} seat(s) available",
    ]
    if candidate.wheelchair_assist_available:
        factors.append("Wheelchair assistance available")
    if candidate.step_free_boarding:
        factors.append("Step-free boarding")
    if candidate.seat_fee_estimate:
        factors.append(f"Estimated seat selection fees: {candidate.seat_fee_estimate:.2f} {currency}")

    return Option(
        category="flight",
        # Where this leg lands. The shared key a package uses to pair the flight
        # with the transfer that actually serves that airport.
        airport=candidate.dest_airport,
        # The same values the description sentence already carried, given
        # structure so the card can show them the way an airline site does.
        schedule=OptionSchedule(
            reference=candidate.flight_id,
            depart=candidate.dep_ts,
            arrive=candidate.arr_ts,
            dest_code=candidate.dest_airport,
            # Outbound or return, so a package can take one of each rather than
            # parsing "(Outbound)" back out of the name.
            direction=candidate.direction,
            stops=candidate.stops,
        ),
        name=f"{candidate.flight_id} ({candidate.direction.title()})",
        # No timestamps here: `schedule` carries them, and the card renders
        # them as an airline site would. Repeating them as ISO prose underneath
        # is exactly what the card replaced.
        description=(
            f"{candidate.direction.title()} to {candidate.dest_airport}, "
            + ("non-stop." if candidate.stops == 0
               else f"{candidate.stops} stop{'s' if candidate.stops > 1 else ''}.")
        ),
        # `price` is ONE seat; `seat_fee_estimate` is already the whole
        # party's fee (see its docstring), so only the fare is multiplied.
        # This is what the party is asked to pay, and what `recommend_package`
        # sums into "closest to your budget".
        estimated_cost=candidate.price * party_size + candidate.seat_fee_estimate,
        # One seat, for the tier columns — the figure an airline quotes and the
        # only one comparable across Budget, Comfort and Luxury. The party seat
        # fee is deliberately excluded: it is a whole-party total, so dividing
        # it out would invent a per-seat number nobody charges.
        unit_cost=candidate.price,
        currency=currency,
        assumptions=[assumption],
        limitations=_accessibility_limitations(candidate),
        selection_factors=factors,
    )


def _coverage_warnings(proposal: FlightProposal) -> list[str]:
    """Name an empty leg explicitly.

    A proposal with an outbound but no return is a partial answer, and saying
    so is more useful to the orchestrator than a shorter option list it has to
    infer the meaning of.
    """
    return [
        f"No {direction.lower()} flight in the loaded inventory satisfies these "
        "dates and constraints."
        for direction in ("OUTBOUND", "RETURN")
        if not any(c.direction == direction for c in proposal.candidates)
    ]


def _build_finding(
    proposal: FlightProposal, response, currency: str, unresolved: list[str], assumption: str,
    party_size: int = 1,
) -> AgentFinding:
    warnings = list(unresolved) + _coverage_warnings(proposal)
    if response.escalate and response.escalation_reason:
        warnings.append(f"Escalation requested: {response.escalation_reason}")
    if response.acknowledgment_applied is not None:
        warnings.append(
            f"The soft preference '{response.acknowledgment_applied.field}' could not be "
            f"satisfied by any available option: {response.acknowledgment_applied.reason}"
        )
    return AgentFinding(
        agent=NAME,
        summary=response.rationale,
        options=[
            _candidate_to_option(c, currency, assumption, party_size)
            for c in proposal.candidates
        ],
        warnings=warnings,
        confidence=response.confidence,
    )


class _EvalRun:
    """One row of `flight_agent_eval_runs`, assembled as the request unfolds.

    Three outcomes end a request — the Path 2 fallback, a completed plan, and an
    exception — and each used to build its own row inline. The fields they share
    (the resolved mode, provider and model; elapsed time; whether the loop ran)
    were therefore restated three times, and every column this table got wrong in
    production got it wrong here: `llm_provider`/`llm_model` recorded as
    `auto`/NULL, `provider_calls` zeroed on the single-shot path, and
    `escalation_reason` claiming a loop that had been switched off.

    So the row is built in one place and the node only says which ending
    happened. `escalation_reason` in particular is derived here from `mode` and
    `escalated_to_loop` rather than re-expressed at the call site, because it is
    a fact about those two values and nothing else.

    Timing starts at construction, which must therefore be the first thing the
    node does — `latency_ms` is meant to cover the whole node, including the
    adapter and the inventory fetch, not just the model call.
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
        # Set once the mode gate has run. False is the correct default: an
        # exception raised before the decision did not escalate to anything.
        self.escalated_to_loop = False
        self.signals = _EvalSignals()

    def _escalation_reason(self) -> str | None:
        """Why the loop ran, or None if it did not.

        `and self.escalated_to_loop` on both arms, because a model without tool
        calling turns the loop off after the mode is resolved; without it the row
        said the loop ran for its own reason while `escalated_to_loop` stayed 0.
        """
        if not self.escalated_to_loop:
            return None
        if self._mode == "auto":
            return "empty_leg"
        if self._mode == "agentic":
            return "mode_agentic"
        return None

    def _write(self, **fields) -> None:
        """Best-effort. Never raises — see `save_flight_eval_run`'s docstring:
        telemetry must not turn an already-served (or already-failed) request
        into a second failure."""
        if not self._tracer.database_path:
            return
        try:
            save_flight_eval_run(
                self._tracer.database_path,
                run_type="production",
                request_id=self._request_id,
                flight_agent_mode=self._mode,
                inventory_source=self._inventory_source,
                llm_provider=self._llm_provider,
                llm_model=self._llm_model,
                trace_id=self._request_id,
                latency_ms=int((time.perf_counter() - self._started) * 1000),
                **fields,
            )
        except Exception as exc:  # noqa: BLE001 - telemetry must never break the response
            self._tracer.record("agent_eval_log_failed", NAME, {"error_type": type(exc).__name__})

    def path2_fallback(self, *, options_returned: int) -> None:
        """No inventory covered the trip, so the prompt-only path answered.

        No guardrail signals: that path runs the shared L1/L2 screening rather
        than this agent's own, and recording its own gates as 'pass' would claim
        checks that never ran.
        """
        self._write(outcome="path2_fallback", options_returned=options_returned)

    def success(
        self, *, budget_exhausted: bool, counts: dict, usage: dict, options_returned: int,
    ) -> None:
        """A finding was produced.

        `budget_exhausted` is still a served request — the distinction is that an
        early-stopped search is not the result a full search would have given, so
        it must not be averaged in with clean runs.
        """
        self._write(
            outcome="budget_exhausted" if budget_exhausted else "success",
            escalated_to_loop=self.escalated_to_loop,
            escalation_reason=self._escalation_reason(),
            # Real on both paths: the inventory fetch goes through `ctx.cache`,
            # which spends a provider call, and on Duffel that is a billed
            # search. `llm_turns` and `tool_calls` are genuinely 0 without the
            # loop — `run_flight_agent` calls the model without spending this
            # budget and does not report how many calls it made.
            llm_turns=counts.get("llm_turns", 0),
            tool_calls=counts.get("tool_calls", 0),
            provider_calls=counts.get("provider_calls", 0),
            usage=usage,
            options_returned=options_returned,
            **self.signals.as_row(),
        )

    def error(self, exc: BaseException, *, usage: dict) -> None:
        self._write(
            outcome="error", error_type=type(exc).__name__,
            escalated_to_loop=self.escalated_to_loop,
            usage=usage,
            **self.signals.as_row(),
        )


def create_node(llm, tracer, provider=None, config=None):
    """The graph node. Grounded when inventory covers the trip, prompt-only otherwise.

    `provider` and `config` are injection points for tests and scripts;
    `graph.build_travel_graph` calls this with `(llm, tracer)` like every other
    specialist factory, and the provider is resolved from `Config` once, here,
    rather than per request.
    """
    # Both hooks have existed on `make_specialist_node` since it was written and
    # this call site had never used either, so the fallback path ran with no input
    # screening and no output screening at all — design.md §6's "Path 2: none"
    # column. `INSTRUCTION` stays the agent's registry description; the node runs
    # PATH2_INSTRUCTION, which asks only for route-level guidance.
    prompt_only_node = make_specialist_node(
        NAME, PATH2_INSTRUCTION, llm, tracer,
        preflight=make_preflight(NAME),
        postprocess=_forbid_concrete_options(NAME, tracer),
    )
    provider_note = None
    if provider is None:
        provider, provider_note = get_inventory_provider(
            vars(Config) if config is None else config
        )
    assumption = getattr(provider, "assumption", INVENTORY_ASSUMPTION)
    # Limits resolved once here, like the provider, then a fresh per-request
    # budget taken from them inside the node — spend must never be shared
    # between travellers.
    settings = vars(Config) if config is None else config
    budget_limits, max_date_shift_days = budget_from_config(settings)
    # Three modes — `structured`, `agentic`, `auto` — resolved once HERE rather
    # than per request, so the mode cannot change under a traveller mid-plan.
    # What each one means, and why `auto` is the default, is in `reasoners.py`
    # beside the code that acts on it; `docs/flight_agent/mode-eval.md` has the
    # measurement the default rests on.
    mode = str(settings.get("FLIGHT_AGENT_MODE", "auto")).strip().lower()
    # Resolved once here, same as `mode` and `provider` — eval logging records
    # what actually ran, not what the current settings say, so a config change
    # mid-deployment doesn't retroactively relabel earlier rows.
    #
    # Through `get_llm_settings`, not the raw keys: LLM_PROVIDER defaults to
    # "auto" and LLM_MODEL is usually blank, so reading them directly recorded
    # provider="auto", model=NULL on a deployment that in fact ran openai/gpt-5.
    # These columns exist to compare runs across providers and models, which
    # "auto"/NULL cannot do. A configuration error leaves both None rather than
    # failing: this is telemetry, and the request itself fails elsewhere.
    _llm_settings, _llm_settings_error = get_llm_settings(settings)
    llm_provider_name = (_llm_settings or {}).get("provider")
    llm_model_name = (_llm_settings or {}).get("model")

    def flight_node(state) -> dict:
        # First statement in the node: the recorder starts its clock here, and
        # `latency_ms` is meant to cover everything the node does.
        eval_run = _EvalRun(
            tracer, state["request_id"], mode=mode, inventory_source=provider.name,
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
        adapted = to_flight_request(travel_request)
        notes = [*adapted.unresolved, *([provider_note] if provider_note else [])]

        # One fetch per request, reused by both the proposal and the screening
        # trace below. A live provider bills and blocks on every call, so the
        # two must never independently ask for the same inventory — and they
        # must see the identical row set, or the explainability trace would
        # describe a different search than the one that produced the options.
        #
        # Routed through `InventoryCache` so that invariant is enforced by a
        # component rather than by this function being careful. The cache is also
        # what generalises it once searching becomes multi-step: it deduplicates
        # repeated searches, caps provider calls, and accumulates `seen_ids` as
        # the provenance ledger. Here it still makes exactly one call, which
        # `test_flight_node_providers.py` pins.
        budget = budget_limits.fresh().start()
        # Set when the loop actually hits a spend cap, so eval logging can
        # call the outcome 'budget_exhausted' rather than a plain 'success' —
        # the run still returns a usable finding, but an early-stopped search
        # is not the same result a full search would have produced.
        budget_exhausted: list[str] = []

        def _on_budget_exhausted(limit: str) -> None:
            budget_exhausted.append(limit)
            tracer.record(EVENT_BUDGET_EXHAUSTED, NAME, {"limit": limit, **budget.as_counts()})

        # One context for the whole request. The loop, when it runs, uses this
        # same cache — building it a second one there would re-fetch inventory
        # already paid for, which is free on seed and billed on Duffel.
        ctx = ToolContext.for_request(
            adapted.request, provider, budget,
            max_date_shift_days=max_date_shift_days,
            on_budget_exhausted=_on_budget_exhausted,
        )
        inventory = []
        if provider.covers(adapted.request):
            inventory, fetch_notes = ctx.cache.rows_for(adapted.request)
            notes.extend(fetch_notes)

        if not inventory:
            # Nothing to ground an answer in — an unstocked route, an
            # unroutable country, or a live search that failed or came back
            # empty. Use the shared prompt-only path, then mark the result as
            # estimated so it is never mistaken for inventory-backed output.
            tracer.record("agent_fallback", NAME, {
                "reason": "no inventory for route/dates",
                "source": provider.name,
                "unresolved": notes,
            })
            result = prompt_only_node(state)
            for finding in result.get("findings", []):
                finding.warnings = [*finding.warnings, *notes, ESTIMATE_WARNING]
            eval_run.path2_fallback(
                options_returned=sum(len(f.options) for f in result.get("findings", [])),
            )
            return result

        tracer.record("agent_started", NAME, {
            "mode": mode, "source": provider.name,
        })
        if tracer.database_path:
            save_agent_run(tracer.database_path, state["request_id"], NAME, "processing")

        usage = TokenUsageCallback()
        try:
            # Which way of thinking this request gets. `reasoners.py` owns the
            # decision and the two implementations; from here on the node does
            # not know or care which one it is holding.
            reasoner = select_reasoner(mode, ctx, inventory, llm, tracer=tracer)
            eval_run.escalated_to_loop = reasoner.is_loop
            outcome = reasoner.run(
                ctx, inventory, llm, tracer=_InnerTracer(tracer, eval_run.signals),
            )
            notes.extend(note for note in outcome.notes if note not in notes)
            finding = _build_finding(
                outcome.proposal, outcome.response, travel_request.currency, notes, assumption,
                # From the same trip context `domain` ranked with, not from
                # `travel_request.travellers`, so the displayed cost cannot
                # drift from the ranked one if the adapter's mapping changes.
                party_size=_party_size(ctx.base_request.trip_context),
            )
            log_payload(f"REQUEST {state['request_id']} | {NAME.upper()} RESPONSE", finding)
            tracer.record("agent_completed", NAME, {
                "mode": reasoner.name,
                "source": provider.name,
                "confidence": finding.confidence,
                "option_count": len(finding.options),
                "warning_count": len(finding.warnings),
                "screened_count": len(outcome.screening),
                "excluded_count": sum(1 for s in outcome.screening if not s.included),
                **usage.as_dict(),
            })
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path, state["request_id"], NAME, "completed",
                    usage.as_dict(), finding,
                )
            eval_run.success(
                budget_exhausted=bool(budget_exhausted),
                counts=ctx.budget.as_counts(),
                usage=usage.as_dict(),
                options_returned=len(finding.options),
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

    return flight_node
