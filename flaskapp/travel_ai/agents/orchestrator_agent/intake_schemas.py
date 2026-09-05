"""Conversational-intake contracts owned by the Orchestrator Agent.

These describe a *partial* travel request. `schemas.TravelRequest` remains the
only complete, validated contract; nothing here bypasses it.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Gender = Literal["female", "male", "non_binary", "prefer_not_to_say"]

InputKind = Literal["country", "text", "date", "number", "gender"]


class ExtractedIntent(BaseModel):
    """What the user has stated so far. Every field is optional by design.

    `None` inside a per-traveller list marks a slot that is still a gap, which is
    what lets two travellers with one known age produce exactly one indexed
    question rather than two.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    origin: str | None = None
    # `destination` is the COUNTRY and `destination_city` the city within it.
    # The hotel adapter reads them that way (`adapter.py:73-74`) and `find_city`
    # is scoped by country, so a city here resolves to nothing and the agent
    # falls back to its prompt-only path with every option stripped.
    destination: str | None = None
    destination_city: str | None = None
    departure_date: date | None = None
    return_date: date | None = None
    travellers: int | None = Field(default=None, ge=1, le=20)
    budget: float | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    preferences: list[str] = Field(default_factory=list, max_length=30)
    accessibility_needs: list[str] = Field(default_factory=list, max_length=30)
    risk_tolerance: Literal["low", "medium", "high"] | None = None
    traveller_ages: list[int | None] = Field(default_factory=list, max_length=20)
    traveller_genders: list[Gender | None] = Field(default_factory=list, max_length=20)
    traveller_accessibility_needs: list[list[str] | None] = Field(
        default_factory=list, max_length=20
    )


class IntakeExtraction(BaseModel):
    """The model's structured reply: what it read, and how it says so."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(max_length=400)
    intent: ExtractedIntent


class MissingField(BaseModel):
    """One thing the orchestrator still needs before it can delegate."""

    name: str
    label: str
    input: InputKind
    traveller_index: int | None = None
    hint: str | None = None

    @property
    def key(self) -> str:
        """The flat answer key: `budget`, or `traveller_ages.0`."""
        return self.name if self.traveller_index is None else f"{self.name}.{self.traveller_index}"


class IntentResponse(BaseModel):
    """The intake endpoints' reply shape."""

    complete: bool
    question: str
    extracted: ExtractedIntent
    missing: list[MissingField]
    request: dict | None = None
    request_id: str | None = None
