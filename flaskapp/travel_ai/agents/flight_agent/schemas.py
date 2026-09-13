"""Flight Agent contracts.

Field names and semantics follow localfolder/db_schema.md sections 2 (Flight
Agent tables) and 6 (A2A message contract) exactly, so this stays a drop-in
fit once the team wires in the real event bus and picks a database engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SeatInventory(BaseModel):
    """Per-flight seat availability, modelled as aggregate COUNTS, not a seat
    map (localfolder/discussion_agents_vs_deterministic.md §7). We model seat
    availability as a flight-*selection* factor; we do not assign actual seats
    (that's a downstream airline seat-map API, documented not built). Stated
    limitation: counts can confirm "≥2 window seats exist" and "an adjacent
    block of 4 exists" but not both *of the same seats* — real geometry needs
    a map.

    Fee model: `standard_fee` is what it costs to *select* a standard seat
    (0 = free selection). `accessible_available` seats are ALWAYS FREE — the
    disability tax is about a required accommodation being mandatory-not-
    optional, so charging for it (even at parity with extra-legroom) just
    relocates the bias. Exit-row seats legally exclude passengers needing
    wheelchair assistance (e.g. FAA 14 CFR 121.585), enforced in domain.py.
    """

    model_config = ConfigDict(extra="forbid")

    window_available: int = Field(ge=0)
    aisle_available: int = Field(ge=0)
    max_adjacent_block: int = Field(ge=0)  # largest run of adjacent free seats
    accessible_available: int = Field(ge=0)  # always free to book
    standard_fee: float = Field(ge=0)  # per-seat fee to select a standard seat
    extra_legroom_available: int = Field(ge=0)
    extra_legroom_fee: float = Field(ge=0)
    exit_row_available: int = Field(ge=0)
    exit_row_fee: float = Field(ge=0)


class FlightInventoryItem(BaseModel):
    """One row of Flight Agent's own inventory (db_schema.md `flight_inventory`).

    `dep_ts` is origin-local ISO 8601 with UTC offset; `arr_ts` is
    destination-local ISO 8601 with UTC offset (check-in feasibility math
    depends on this distinction — db_schema.md rule 4).

    On the two accessibility booleans being nullable: `None` means "this
    supplier does not publish it", NOT "not available". The distinction is
    load-bearing. Seed rows always state a real True/False because the dataset
    was written to. A live GDS feed (Duffel, in `providers/duffel.py`) has no
    such field at all, and defaulting an absent field to True would invent an
    accessibility guarantee for the traveller least able to absorb the cost of
    it being wrong, while defaulting to False would hide every live flight from
    the same traveller. `domain.py` therefore treats `None` as a third case:
    not excluded on the implicit filter, excluded under an explicit
    `require_wheelchair_assist` constraint, and always surfaced to the
    traveller as unverified rather than silently passed off as checked.
    """

    model_config = ConfigDict(extra="forbid")

    flight_id: str
    carrier: str
    flight_no: str
    origin_airport: str
    dest_airport: str
    dep_ts: str
    arr_ts: str
    duration_min: int = Field(gt=0)
    price: float = Field(gt=0)
    cabin_class: str
    seats_available: int = Field(ge=0)
    stops: int = Field(ge=0)
    # None = the source does not publish this. See the class docstring.
    wheelchair_assist_available: bool | None
    step_free_boarding: bool | None
    # Which provider produced this row. Drives the traveller-facing assumption
    # text (illustrative dataset vs. live, expiring fare) — see agent.py.
    source: Literal["seed", "duffel"] = "seed"
    # None = seat selection not modelled for this flight (older rows / ad-hoc
    # test flights). All seat filters and fees are skipped when this is None,
    # which is what keeps the whole seat feature backward-compatible.
    seat_inventory: SeatInventory | None = None

    @property
    def route(self) -> str:
        """Derived join key, e.g. `SIN#NRT` — not stored redundantly."""
        return f"{self.origin_airport}#{self.dest_airport}"


class FlightHold(BaseModel):
    """db_schema.md `flight_holds` — one row per session per held flight."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    flight_id: str
    saga_id: str
    direction: Literal["OUTBOUND", "RETURN"]
    status: Literal["HELD", "CONFIRMED", "RELEASED"]
    price_at_hold: float = Field(gt=0)
    hold_expires_at: str
    created_at: str


class ArrivalPreference(BaseModel):
    """A traveller-stated arrival deadline, captured at intake (`trip.context`,
    round 0) — distinct from `FlightConstraints.arrive_before`, which is
    orchestrator-issued mid-negotiation (e.g. from Hotel's check-in-feasibility
    finding). `hard=True` excludes later options entirely (a business
    traveller's meeting); `hard=False` only ranks earlier arrivals higher
    without excluding anything (a soft preference).
    """

    model_config = ConfigDict(extra="forbid")

    direction: Literal["OUTBOUND", "RETURN"]
    by: str
    hard: bool = False


class FlightPreferences(BaseModel):
    """Structured traveller preferences for ranking/filtering, populated
    upstream (a UI form, or an intake-parsing step turning free text like "I
    have a 2pm meeting" into `ArrivalPreference(hard=True)`) — Flight Agent
    only ever consumes these structured fields, never raw free text. Keeps
    ranking deterministic/explainable and keeps the one place that has to
    handle untrusted free text upstream of every agent, not duplicated in each.
    """

    model_config = ConfigDict(extra="forbid")

    prefer_direct: bool = False
    avoid_red_eye: bool = False
    max_stops: int | None = Field(default=None, ge=0)
    arrival_preferences: list[ArrivalPreference] = Field(default_factory=list)

    # Seat preferences (discussion_agents_vs_deterministic.md §7).
    # `must_sit_together`: HARD — excludes flights whose largest adjacent block
    # can't seat the whole party. UI suggests this on when party has children,
    # user decides. `seat_configuration`: SOFT — e.g. {"window": 2, "aisle": 2};
    # ranks flights that can satisfy it higher, never excludes (also a valid
    # option-B acknowledgment target). `seat_tier_preference`: SOFT — which fare
    # tier's fee applies; EXIT_ROW is invalid when wheelchair assistance is
    # needed (enforced in domain.py, not here — this schema doesn't see
    # accessibility_needs). An accessible seat REQUIREMENT is NOT a preference:
    # it's derived from trip_context.accessibility_needs and enforced as a hard
    # filter, so a wheelchair user never has to ask for it twice.
    must_sit_together: bool = False
    seat_configuration: dict | None = None
    seat_tier_preference: Literal["STANDARD", "EXTRA_LEGROOM", "EXIT_ROW"] | None = None


CHILD_AGE_LIMIT = 18  # under this age counts as a child when deriving `party`


class TripContext(BaseModel):
    """`trip.context` message payload — sourced from `sessions` (db_schema.md §6).

    Reconciled against the live intake form (`templates/main.html` +
    `static/js/app.js`'s `buildPayload`) on 2026-08-05. The form is the only
    real source of trip data today, so anything it collects that Flight Agent
    can legitimately use is represented here; anything it does not collect is
    optional, with a documented reason. `adapter.py` is the single place that
    performs the translation — keep the mapping there, not in domain logic.

    On `traveller_genders`: flight selection has NO legitimate use for gender.
    Nothing in `domain.py` reads it, and nothing ever should — schedule, price,
    stops, seats and accessibility are the only inputs to ranking. It is
    carried here for one purpose only: it reaches the LLM in `reasoning.py`'s
    context, which is what makes an XRAI bias audit possible at all. Holding
    every other field constant and varying only this one shows whether the
    model's rationale shifts on a protected attribute it was never given a
    reason to use. That test cannot be run on a field the agent never sees.

    The risk is real and accepted deliberately: a field in the model's context
    is a field the model can condition on. That is the finding the audit is
    looking for, not a side effect to be designed away. Two consequences
    follow, both enforced rather than assumed — `test_flight_bias_audit.py`
    asserts that deterministic ranking is byte-identical across genders, so a
    difference can only ever come from the LLM layer; and if the audit is ever
    retired, this field should be dropped with it rather than left behind.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Route ---------------------------------------------------------------
    # The form collects a COUNTRY and a CITY (a dependent <select> pair over
    # countries.py and places.py). `airports.py` resolves that pair to every
    # airport serving the city — a list, not a scalar, because a traveller who
    # picks Tokyo means the city and should see Haneda fares alongside Narita.
    #
    # Country fields are retained alongside the city because Risk & Advisory
    # reasons at country granularity (visas, advisories) and the stored request
    # is keyed on them. Airports are optional so a request naming a city with
    # no mapping still validates — domain.py simply finds no candidates, which
    # is the honest result, not a crash.
    #
    # `origin_airport`/`dest_airport` are the PRIMARY gateway, kept as scalars
    # for display and for callers that can only use one code. Route matching
    # must use the `_airports` lists instead; using the scalar there would
    # silently reintroduce the single-gateway limitation cities exist to fix.
    origin_country: str | None = None
    dest_country: str
    origin_city: str | None = None
    dest_city: str | None = None
    origin_airport: str | None = None
    dest_airport: str | None = None
    origin_airports: list[str] = Field(default_factory=list)
    dest_airports: list[str] = Field(default_factory=list)

    depart_date: str
    return_date: str

    # --- Party ---------------------------------------------------------------
    # `party` stays the field domain.py reads (unchanged ranking behaviour).
    # `traveller_ages` is what the form actually collects, so it is the input of
    # record and `party` is derived from it when not supplied explicitly.
    party: dict = Field(default_factory=lambda: {"adults": 1, "children": 0})
    traveller_ages: list[int] = Field(default_factory=list)
    # Protected attribute. Present for bias auditing only — see the class
    # docstring. Never reference this in ranking, filtering, or fee logic.
    traveller_genders: list[str] = Field(default_factory=list)

    budget_total: float | None = None
    currency: str = Field(default="SGD", pattern=r"^[A-Z]{3}$")

    # Trip-level needs, plus the per-traveller breakdown the form collects.
    # Flight Agent needs the breakdown for seat filtering: one wheelchair user
    # in a party of four requires one accessible seat, not four.
    accessibility_needs: list[str] = Field(default_factory=list)
    traveller_accessibility_needs: list[list[str]] = Field(default_factory=list)

    # Not collected by the trip form. Available from the signed-in user's
    # profile (`users.country`), which the adapter passes through when known.
    passport_country: str | None = None

    # The form sends a comma-split list of free text; earlier drafts assumed a
    # dict. Both are accepted so this stays compatible with either producer.
    preferences: dict | list[str] = Field(default_factory=dict)
    # Follow-up instructions from the "Refine your plan" panel. Free text, and
    # therefore screened exactly like `preferences` before reaching any model.
    refinement_notes: list[str] = Field(default_factory=list)

    flight_preferences: FlightPreferences = Field(default_factory=FlightPreferences)

    @model_validator(mode="after")
    def reconcile_airport_scalars_and_lists(self) -> "TripContext":
        """Keep the scalar gateway and the airport list in agreement.

        Callers supply one or the other. `adapter.py` sets the lists from a
        resolved city; tests, golden scenarios and any pre-city caller set only
        the scalar. Filling each from the other means neither kind of caller
        has to know the other exists, which is what lets city support land
        without touching a single golden-scenario fixture.

        The scalar is always the FIRST list entry, never a separate choice, so
        "primary gateway" means the same thing to both.
        """
        if not self.origin_airports and self.origin_airport:
            self.origin_airports = [self.origin_airport]
        if not self.dest_airports and self.dest_airport:
            self.dest_airports = [self.dest_airport]
        if self.origin_airports and not self.origin_airport:
            self.origin_airport = self.origin_airports[0]
        if self.dest_airports and not self.dest_airport:
            self.dest_airport = self.dest_airports[0]
        return self

    @model_validator(mode="after")
    def derive_party_from_ages(self) -> "TripContext":
        """Fill `party` from `traveller_ages` when the caller did not set it.

        An explicitly supplied `party` always wins — callers that already know
        their breakdown (tests, the golden scenarios, an upstream agent) are
        never second-guessed. Only the default is replaced.
        """
        if self.traveller_ages and self.party == {"adults": 1, "children": 0}:
            children = sum(1 for age in self.traveller_ages if age < CHILD_AGE_LIMIT)
            self.party = {
                "adults": len(self.traveller_ages) - children,
                "children": children,
            }
        return self


class FlightConstraints(BaseModel):
    """Present only on renegotiation (`conflict.notice` → re-proposal).

    `direction` scopes the constraint to one leg (the one that triggered the
    conflict, e.g. Hotel flags the outbound arrival as infeasible) — the
    other leg still falls back to the implicit accessibility-needs filter.
    Omitting `direction` applies the constraint to both legs.
    """

    model_config = ConfigDict(extra="forbid")

    direction: Literal["OUTBOUND", "RETURN"] | None = None
    arrive_before: str | None = None
    require_wheelchair_assist: bool = False
    max_price: float | None = None


class NegotiationRoundSummary(BaseModel):
    """One prior round's outcome, orchestrator-fed context — this is Flight
    Agent's "memory": it doesn't own any negotiation-state storage itself
    (that's `negotiation_state`, orchestrator-owned per db_schema.md §1), but
    its reasoning is informed by what happened in earlier rounds because the
    orchestrator hands this in with each request.
    """

    model_config = ConfigDict(extra="forbid")

    round: int
    proposed_flight_ids: list[str]
    outcome: str  # e.g. "ACCESSIBILITY_VETOED", "BUDGET_EXCEEDED", "ACCEPTED"
    detail: str


class FlightProposalRequest(BaseModel):
    """`flight.proposal.request` — Orchestrator → Flight."""

    model_config = ConfigDict(extra="forbid")

    trip_context: TripContext
    constraints: FlightConstraints | None = None
    negotiation_history: list[NegotiationRoundSummary] = Field(default_factory=list)


class FlightCandidate(BaseModel):
    """One candidate within `flight.proposal` — Flight → bus."""

    model_config = ConfigDict(extra="forbid")

    flight_id: str
    direction: Literal["OUTBOUND", "RETURN"]
    dep_ts: str
    arr_ts: str
    dest_airport: str
    stops: int
    price: float
    seats: int
    # None = unverified, not unavailable — carried through from
    # FlightInventoryItem so the traveller-facing Option can say which it is.
    wheelchair_assist_available: bool | None
    step_free_boarding: bool | None
    source: Literal["seed", "duffel"] = "seed"
    # Estimated total seat-selection fee for the whole party given their seat
    # preferences (0 if they're not selecting seats). Exposed so the Budget
    # Validator sees the TRUE trip cost — this is what lets seat preferences
    # trigger budget conflicts (discussion_agents_vs_deterministic.md §7).
    seat_fee_estimate: float = 0.0


class FlightProposal(BaseModel):
    """`flight.proposal` — Flight → bus. Empty `candidates` means no feasible option."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[FlightCandidate] = Field(default_factory=list)


class FlightScreeningResult(BaseModel):
    """Post-tool traceability record for one inventory row (included or not).

    Closes the gap `db_schema.md`'s explainability substrate requires — every
    claim in the final "why this itinerary" narrative must be traceable, which
    means rejections need a reason on record too, not just survivors. See
    localfolder/discussion_18Jul.md session notes (XRAI notebook synthesis,
    2026-07-19) for where this pattern came from.
    """

    model_config = ConfigDict(extra="forbid")

    flight_id: str
    direction: Literal["OUTBOUND", "RETURN"]
    included: bool
    reasons: list[str] = Field(default_factory=list)


class PreferenceAcknowledgment(BaseModel):
    """A structured, code-validated admission that one of Flight Agent's own
    soft preferences could not be honoured (option B —
    localfolder/discussion_agents_vs_deterministic.md). Despite the verb this
    replaced ("relax"), applying this NEVER changes which flights are shown or
    their order — soft preferences never excluded or reordered anything a
    hard filter didn't already settle, so there is nothing left to loosen.
    What it changes is disclosure: the traveller is told the wish could not be
    met, instead of it being silently dropped.

    This is the LLM's *only* permitted authority over control flow, and the
    Literal below is the actual enforcement of that fence: the LLM cannot
    even express a request to touch max_stops, a hard arrival preference,
    accessibility, or budget — those aren't valid values of `field`, so
    Pydantic rejects them before this ever reaches domain.py.
    `domain.acknowledgment_is_valid()` re-verifies against a real, unanimous
    gap before anything acts on this — never trust the LLM's own claim.
    """

    model_config = ConfigDict(extra="forbid")

    field: Literal["avoid_red_eye", "prefer_direct", "soft_arrival_preference"]
    direction: Literal["OUTBOUND", "RETURN"] | None = None
    reason: str

    @model_validator(mode="after")
    def direction_required_for_arrival_preference(self) -> "PreferenceAcknowledgment":
        if self.field == "soft_arrival_preference" and self.direction is None:
            raise ValueError("direction is required when field='soft_arrival_preference'")
        return self


class FlightAgentResponse(BaseModel):
    """The LLM 'brain' layer's structured output — reasoning over
    `propose_flights()`'s already-computed, grounded proposal. Never
    restates flight facts (price/seats/times, those live in FlightCandidate
    already); only cites flight_ids that exist in the proposal it was given.
    `guardrails.validate_grounded_explanation()` enforces this before any
    caller trusts `highlighted_flight_ids`.
    """

    model_config = ConfigDict(extra="forbid")

    rationale: str
    highlighted_flight_ids: list[str] = Field(default_factory=list)
    escalate: bool = False
    escalation_reason: str | None = None
    suggested_acknowledgment: str | None = Field(
        default=None,
        description="Free-text explanation of why acknowledging one soft "
        "preference as unmet might help — human-readable companion to the "
        "structured proposed_acknowledgment below, which is the field code "
        "actually acts on.",
    )
    proposed_acknowledgment: PreferenceAcknowledgment | None = Field(
        default=None,
        description="A concrete, bounded admission that one of Flight "
        "Agent's own soft preferences could not be honoured — only takes "
        "effect if domain.acknowledgment_is_valid() confirms every "
        "surviving flight really does fail it.",
    )
    acknowledgment_applied: PreferenceAcknowledgment | None = Field(
        default=None,
        description="Set by code (agent.py), never by the LLM, after "
        "validating and applying a proposed_acknowledgment — the audit "
        "record of what option B actually did, if anything. Never changes "
        "which flights are shown; it only changes what the traveller is told.",
    )
    confidence: float = Field(ge=0, le=1)
