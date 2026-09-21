"""Internal, typed contracts for accessibility planning and evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AccessibilityCategory = Literal[
    "mobility", "vision", "hearing", "cognitive", "service_animal",
    "medical_equipment", "dietary", "other",
]


class AccessibilityRequirement(BaseModel):
    requirement_id: str
    traveller_index: int | None = None
    category: AccessibilityCategory
    description: str
    critical: bool = True


class AccessibilitySearchPlan(BaseModel):
    destination: str
    requirements: list[AccessibilityRequirement] = Field(default_factory=list)
    journey_segments: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)


class AccessibilityEvidence(BaseModel):
    evidence_id: str
    title: str
    url: str
    excerpt: str
    relevance: float = Field(ge=0, le=1)
    source_type: Literal["official", "specialist", "crowdsourced", "unknown"]
    query_scope: str
    published_or_updated_at: str | None = None
    freshness: Literal["current", "stale", "unknown"] = "unknown"

