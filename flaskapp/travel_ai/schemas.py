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
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    # City names as displayed in `flaskapp/places.py`, which the form posts
    # alongside the country. Left unvalidated against that dataset on purpose:
    # an unknown city is reported as unroutable by the flight adapter, in
    # keeping with "no route" being an answer the orchestrator can negotiate
    # around rather than an exception it has to catch.
    origin_city: str | None = Field(default=None, min_length=2, max_length=100)
    destination_city: str | None = Field(default=None, min_length=2, max_length=100)
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


class Option(BaseModel):
    """A transparent option proposed by a specialist agent."""

    name: str
    description: str
    estimated_cost: float | None = None
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


class PlanResponse(BaseModel):
    request_id: str
    status: Literal["completed"] = "completed"
    plan: TravelPlan
    agent_findings: list[AgentFinding]
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
