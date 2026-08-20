"""Hotel & Transport Agent contracts.

Field names and semantics follow localfolder/db_schema.md sections 3 (Hotel
Agent tables) and 6 (A2A message contract) exactly, so this stays a drop-in
fit once the team wires in the real event bus and picks a database engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HotelInventoryItem(BaseModel):
    """One row of Hotel Agent's inventory.

    `city_slug` is the foreign key into `flaskapp/places.py` — it is the stable
    identifier the handoff requires, never the display name.
    """

    model_config = ConfigDict(extra="forbid")

    hotel_id: str
    name: str
    city_slug: str
    star_rating: int | None = Field(default=None, ge=1, le=5)
    price_per_night: float = Field(gt=0)
    room_type: str
    distance_to_center_km: float | None = Field(default=None, ge=0)
    # Accessibility booleans: None means "not stated by the source", not False.
    # The house rule is that unknown is not the same as unavailable.
    wheelchair_accessible: bool | None = None
    step_free_entrance: bool | None = None
    accessible_bathroom: bool | None = None
    braille_signage: bool | None = None
    amenities: list[str] = Field(default_factory=list)
    source: Literal["seed", "estimated"] = "seed"


class HotelCandidate(BaseModel):
    """One candidate within `hotel.proposal` — Hotel → bus."""

    model_config = ConfigDict(extra="forbid")

    hotel_id: str
    name: str
    city_slug: str
    star_rating: int | None = None
    price_per_night: float
    room_type: str
    distance_to_center_km: float | None = None
    wheelchair_accessible: bool | None = None
    step_free_entrance: bool | None = None
    accessible_bathroom: bool | None = None
    amenities: list[str] = Field(default_factory=list)
    source: Literal["seed", "estimated"] = "seed"
    # Total estimated cost for the stay (price_per_night * nights)
    estimated_total_cost: float | None = None
    currency: str | None = None


class HotelPreferences(BaseModel):
    """Structured traveller preferences for hotel ranking/filtering."""

    model_config = ConfigDict(extra="forbid")

    min_star_rating: int | None = Field(default=None, ge=1, le=5)
    max_price_per_night: float | None = Field(default=None, gt=0)
    room_type: str | None = None
    accessibility_needs: list[str] = Field(default_factory=list)
    prefer_center: bool = False
    must_be_accessible: bool = False


CHILD_AGE_LIMIT = 18


class HotelTripContext(BaseModel):
    """Context for hotel search."""

    model_config = ConfigDict(extra="forbid")

    dest_country: str | None = None
    dest_city: str | None = None
    dest_city_slug: str | None = None
    dest_airports: list[str] = Field(default_factory=list)
    arrival_airport: str | None = None  # from flight agent findings
    check_in_date: str
    check_out_date: str
    party: dict = Field(default_factory=lambda: {"adults": 1, "children": 0})
    traveller_ages: list[int] = Field(default_factory=list)
    budget_total: float | None = None
    currency: str = Field(default="SGD", pattern=r"^[A-Z]{3}$")
    accessibility_needs: list[str] = Field(default_factory=list)
    preferences: dict | list[str] = Field(default_factory=dict)
    refinement_notes: list[str] = Field(default_factory=list)
    hotel_preferences: HotelPreferences = Field(default_factory=HotelPreferences)

    @model_validator(mode="after")
    def derive_party_from_ages(self) -> "HotelTripContext":
        if self.traveller_ages and self.party == {"adults": 1, "children": 0}:
            children = sum(1 for age in self.traveller_ages if age < CHILD_AGE_LIMIT)
            self.party = {
                "adults": len(self.traveller_ages) - children,
                "children": children,
            }
        return self

    @property
    def nights(self) -> int:
        try:
            check_in = __import__("datetime").date.fromisoformat(self.check_in_date)
            check_out = __import__("datetime").date.fromisoformat(self.check_out_date)
            return max(1, (check_out - check_in).days)
        except (TypeError, ValueError):
            return 1


class HotelConstraints(BaseModel):
    """Present only on renegotiation."""

    model_config = ConfigDict(extra="forbid")

    max_price_per_night: float | None = None
    require_wheelchair_accessible: bool = False


class HotelProposalRequest(BaseModel):
    """`hotel.proposal.request` — Orchestrator → Hotel."""

    model_config = ConfigDict(extra="forbid")

    trip_context: HotelTripContext
    constraints: HotelConstraints | None = None
    flight_candidates: list[dict] = Field(default_factory=list)


class HotelProposal(BaseModel):
    """`hotel.proposal` — Hotel → bus."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[HotelCandidate] = Field(default_factory=list)


class HotelScreeningResult(BaseModel):
    """Explainability record for one hotel inventory row."""

    model_config = ConfigDict(extra="forbid")

    hotel_id: str
    included: bool
    reasons: list[str] = Field(default_factory=list)


class TransportOption(BaseModel):
    """One local transport / transfer option."""

    model_config = ConfigDict(extra="forbid")

    name: str
    mode: str
    duration_minutes: int | None = None
    distance_km: float | None = None
    estimated_cost: float | None = None
    currency: str | None = None
    frequency: str | None = None
    accessibility_notes: list[str] = Field(default_factory=list)
    source: Literal["seed", "estimated", "api"] = "seed"
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class HotelTransportResponse(BaseModel):
    """The LLM reasoning layer's structured output."""

    model_config = ConfigDict(extra="forbid")

    rationale: str
    highlighted_hotel_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    escalate: bool = False
    escalation_reason: str | None = None
