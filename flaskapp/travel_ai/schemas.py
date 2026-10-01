"""Validated API, agent, and response contracts."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flaskapp.travel_ai.a2a import A2AMessage


class TravelRequest(BaseModel):
    """User-provided planning constraints. Sensitive traits are intentionally absent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # `origin`/`destination` are COUNTRIES and remain the fields of record:
    # they are NOT NULL in `travel_requests`, the admin dashboard queries them,
    # and Risk & Advisory reasons at country granularity (visas, advisories).
    # The city fields are additive so every request predating city intake —
    # stored rows, golden scenarios, the bias audit — stays valid unchanged.
    # Optional ONLY for a hotel-only request, which is a stay rather than a
    # journey: nobody flies, and Risk & Advisory — the other reader of this
    # field, for visa and entry rules — does not run for that scope. Every
    # other scope still requires it; see `origin_is_required_unless_hotel_only`.
    origin: str | None = Field(default=None, min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    # City names as displayed in `flaskapp/places.py`, which the form posts
    # alongside the country. Left unvalidated against that dataset on purpose:
    # an unknown city is reported as unroutable by the flight adapter, in
    # keeping with "no route" being an answer the orchestrator can negotiate
    # around rather than an exception it has to catch.
    origin_city: str | None = Field(default=None, min_length=2, max_length=100)
    destination_city: str | None = Field(default=None, min_length=2, max_length=100)
    # Which specialists this trip needs. Defaulted, so every stored row, golden
    # scenario and caller written before selective dispatch stays valid — the
    # same additive discipline the city fields used. `both` is the superset, so
    # an absent value runs an agent the traveller may not have needed rather
    # than silently dropping one they did.
    plan_scope: Literal["both", "flights", "hotel"] = "both"
    departure_date: date
    return_date: date
    travellers: int = Field(default=1, ge=1, le=20)
    traveller_ages: list[int] = Field(min_length=1, max_length=20)
    traveller_genders: list[Literal["female", "male", "non_binary", "prefer_not_to_say"]] = Field(
        min_length=1, max_length=20
    )
    traveller_accessibility_needs: list[list[str]] = Field(min_length=1, max_length=20)
    budget: float = Field(gt=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    preferences: list[str] = Field(default_factory=list, max_length=30)
    accessibility_needs: list[str] = Field(default_factory=list, max_length=30)
    refinement_notes: list[str] = Field(default_factory=list, max_length=10)
    risk_tolerance: Literal["low", "medium", "high"] = "medium"

    @model_validator(mode="after")
    def origin_is_required_unless_hotel_only(self) -> "TravelRequest":
        """A journey needs a departure country; a hotel stay does not.

        Enforced here rather than by making the field required, because the
        requirement genuinely depends on another field. A caller that omits
        `origin` on a flight-bearing scope gets a validation error at L0, not a
        plan built around a hole.
        """
        if self.plan_scope != "hotel" and not self.origin:
            raise ValueError("origin is required unless the request is hotel-only")
        return self

    @model_validator(mode="after")
    def dates_are_ordered(self) -> "TravelRequest":
        if self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")
        if len(self.traveller_ages) != self.travellers:
            raise ValueError("one age is required for each traveller")
        if len(self.traveller_genders) != self.travellers:
            raise ValueError("one gender selection is required for each traveller")
        if len(self.traveller_accessibility_needs) != self.travellers:
            raise ValueError("one accessibility entry is required for each traveller")
        if any(age < 0 or age > 120 for age in self.traveller_ages):
            raise ValueError("traveller ages must be between 0 and 120")
        return self


class OptionSchedule(BaseModel):
    """The timed facts an airline site shows, for options that have them.

    Only grounded flights carry one: the prompt-only fallback has no flight
    number and no timestamps to give. `depart` and `arrive` are ISO-8601 with
    each airport's own offset, because they are wall-clock times in two
    different places.
    """

    reference: str
    depart: str
    arrive: str
    dest_code: str = ""
    direction: str = ""          # OUTBOUND | RETURN
    stops: int = 0


class Option(BaseModel):
    """A transparent option proposed by a specialist agent."""

    # What kind of thing this is, asserted by the builder that made it rather
    # than inferred from its prose. Without it a hotel and an airport transfer
    # are indistinguishable in `finding.options` — both are just a name and a
    # description — and rendering them separately would mean parsing the
    # description, a format nothing enforces. Defaulted to None so every stored
    # row and A2A artifact written before this field stays valid.
    category: Literal["flight", "hotel", "transport", "accessibility", "advisory"] | None = None
    # The airport this option lands at, or serves. Set by the flight builder
    # from the candidate's destination and by the transport builder from the
    # pair it came from — it is what lets a package pair a flight with the right
    # transfer, instead of matching on prose in a description.
    airport: str = ""
    # Present only where a specialist had real scheduled inventory to report.
    schedule: OptionSchedule | None = None
    # The same schedule, formatted for display. Set on the COPY that reaches a
    # tier column, never on the option the specialist returned, so
    # `agent_findings` keeps exactly what was found. Formatting lives on the
    # server because the next-day marker has to be computed, not eyeballed.
    schedule_display: dict[str, str] | None = None
    name: str
    description: str
    # What the PARTY pays. For a flight that is the fare times the party size;
    # for a hotel, the rooms the party needs across the stay.
    estimated_cost: float | None = None
    # The same thing for one traveller, where that means anything — a fare is
    # quoted per seat, a hotel room is not. None says "this option has no
    # per-person figure", which is why a tier column can fall back to
    # `estimated_cost` without guessing.
    unit_cost: float | None = None
    currency: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    selection_factors: list[str] = Field(default_factory=list)


class AgentFinding(BaseModel):
    # CUSTOMIZE SPECIALIST OUTPUTS HERE. Structured fields are more reliable and
    # auditable than asking agents to return unstructured paragraphs.
    agent: Literal[
        "flight_agent",
        "hotel_transport_agent",
        "accessibility_agent",
        "risk_advisory_agent",
    ]
    summary: str
    options: list[Option] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class SafetyAssessment(BaseModel):
    passed: bool
    checks: list[str]
    warnings: list[str] = Field(default_factory=list)


def pending_safety_assessment() -> SafetyAssessment:
    """Placeholder replaced by the deterministic post-generation assessment."""
    return SafetyAssessment(passed=False, checks=[], warnings=["Assessment pending"])


class TravelPlan(BaseModel):
    # CUSTOMIZE THE ORCHESTRATOR'S FINAL OUTPUT HERE. The frontend/API will receive
    # these fields after Pydantic validates the model response.
    title: str
    summary: str
    itinerary: list[str]
    estimated_total_cost: float | None = None
    currency: str | None = None
    rationale: list[str] = Field(description="Concise decision factors, not hidden reasoning")
    alternatives: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    safety: SafetyAssessment = Field(default_factory=pending_safety_assessment)


class PlanTier(BaseModel):
    """One price band within a section. An empty `label` means no banding."""

    label: str
    options: list[Option] = Field(default_factory=list)


class PlanRecommendation(BaseModel):
    """One combination that fits the traveller's stated budget, or why none does.

    Always present on a response: when nothing fits, `items` is empty and `note`
    carries the reason. "This trip does not fit your budget, by 400 SGD" is more
    use than silence.
    """

    items: list[Option] = Field(default_factory=list)
    total: float = 0.0
    currency: str = ""
    remaining: float = 0.0
    # How many travellers every figure in `items` covers. Carried so the card
    # can say so: "2559 SGD" beside a flight is a different claim for a solo
    # traveller than for a family of three, and the number alone cannot tell
    # them apart. Defaulted for callers written before this field.
    travellers: int = 1
    note: str = ""


class PlanGroup(BaseModel):
    """One section's contribution to one price tier."""

    title: str
    icon: str = ""
    options: list[Option] = Field(default_factory=list)


class PlanPackage(BaseModel):
    """One tier column, holding every section that contributed to it.

    Not a costed bundle: it shows the cheapest flights beside the cheapest
    hotels and makes no claim that they add up to a trip within budget.
    """

    label: str
    groups: list[PlanGroup] = Field(default_factory=list)


class PlanSection(BaseModel):
    """One specialist's contribution to the plan, as a traveller reads it."""

    title: str
    agent: str
    summary: str
    options: list[Option] = Field(default_factory=list)
    # Both defaulted, so a caller constructing a PlanResponse by hand — and the
    # A2A artifact written before tiers existed — stays valid.
    icon: str = ""
    tiers: list[PlanTier] = Field(default_factory=list)


class PlanResponse(BaseModel):
    request_id: str
    status: Literal["completed"] = "completed"
    plan: TravelPlan
    agent_findings: list[AgentFinding]
    # Derived from `agent_findings` at response time and never persisted: the
    # findings themselves are already stored, and a second copy could disagree
    # with the first. Defaulted so an older caller constructing a PlanResponse
    # by hand stays valid.
    sections: list[PlanSection] = Field(default_factory=list)
    packages: list[PlanPackage] = Field(default_factory=list)
    recommendation: PlanRecommendation = Field(default_factory=PlanRecommendation)
    trace_url: str


def merge_findings(left: list[AgentFinding], right: list[AgentFinding]) -> list[AgentFinding]:
    """LangGraph reducer that preserves findings from all specialists."""
    return left + right


def merge_messages(left: list[A2AMessage], right: list[A2AMessage]) -> list[A2AMessage]:
    """LangGraph reducer that retains the auditable A2A message stream."""
    return left + right


class GraphState(dict):
    """Documentation marker; runtime state is declared as TravelGraphState below."""


from typing_extensions import TypedDict  # noqa: E402 (keeps schema classes grouped)


class TravelGraphState(TypedDict, total=False):
    request_id: str
    request: dict[str, Any]
    findings: Annotated[list[AgentFinding], merge_findings]
    messages: Annotated[list[A2AMessage], merge_messages]
    plan: TravelPlan
    safety_warnings: list[str]
