"""Risk & Advisory Agent's own contracts.

Kept separate from the shared `flaskapp/travel_ai/schemas.py` for the same
reason every other specialist's schemas are: this agent's internal shape is
tuned to its own domain (reference-data grounding) and should not move every
time the shared `AgentFinding`/`TravelRequest` contract does. `adapter.py` is
the only file that knows both.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# Free text, not a Literal/Enum: `risk_standing_facts.csv`'s `category` column
# is deliberately unconstrained so a new advisory category is an inserted
# row, never a code change. Constraining this to a closed Python type would
# just move that same rigidity into a second place.
RiskCategory = str

RiskKind = Literal["standing_fact", "seasonal_window", "dated_event"]


class RiskItem(BaseModel):
    """One fact `domain.py` actually found — never something the model added.

    `risk_id` is the grounding anchor: `reasoning.py` requires every risk the
    model's narrative cites to name one of these, and drops anything that
    doesn't.
    """

    risk_id: str
    kind: RiskKind
    category: RiskCategory
    severity: Literal["low", "medium", "high"] | None = None
    title: str
    detail: str
    mitigation: str | None = None
    applies_to: str | None = None
    source: str = "synthetic reference data — illustrative only"


class RiskProposalRequest(BaseModel):
    """What `domain.py` needs to query the reference tables — nothing more."""

    destination_slug: str | None
    destination: str
    departure_date: date
    return_date: date
    preferences: list[str] = Field(default_factory=list)
    refinement_notes: list[str] = Field(default_factory=list)


class RiskProposal(BaseModel):
    items: list[RiskItem] = Field(default_factory=list)


class RiskAgentResponse(BaseModel):
    """The LLM's structured answer over an already-built `RiskProposal`.

    Mirrors `FlightAgentResponse`'s `escalate`/`escalation_reason` shape
    (structured fields, not a text convention) — see
    `docs/risk_advisory_agent/design.md` §3.5 on why this can't fully replace
    the existing `ESCALATE:` prefix convention without a shared-schema change
    that is out of this agent's authority to make alone.
    """

    rationale: str
    highlighted_risk_ids: list[str] = Field(default_factory=list)
    escalate: bool = False
    escalation_reason: str | None = None
    confidence: float = Field(ge=0, le=1)
