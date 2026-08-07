"""Translation between the shared `TravelRequest` and Flight Agent's contracts.

This is the seam. `flaskapp/travel_ai/schemas.py` is team-shared and changes as
other agents are developed; Flight Agent's own schemas are tuned to flight
domain logic (legs, airports, seat inventory) and should not be dragged along
with every shared-schema edit. Everything that knows about BOTH lives here, so
a change to either side is a change to one file.

What the shared request cannot supply, and how each gap is handled:

| Needed by Flight Agent | In `TravelRequest`? | Handling |
|---|---|---|
| Airport codes | No — countries only | `airports.resolve_airport`, reported when it fails |
| Destination city | No | Left `None`; country granularity is what we have |
| `party` split | Implicit in ages | Derived, under-18 counts as a child |
| `passport_country` | No | Passed in from the signed-in user's profile |
| Structured flight preferences | No — free text | Conservatively derived; see `derive_flight_preferences` |

`traveller_genders` is passed through, against the usual instinct to strip a
protected attribute at the boundary. It exists solely so an XRAI bias audit can
vary it and observe the LLM; no ranking code reads it. See `TripContext`'s
docstring for the full reasoning and the test that holds the line.

Nothing here raises on missing data. An unroutable request yields a context
that produces zero candidates and an `unresolved` list explaining why — a
result the orchestrator can act on, rather than an exception it must catch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flaskapp.travel_ai.agents.flight_agent.airports import has_seed_inventory, resolve_airport
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightPreferences,
    FlightProposalRequest,
    TripContext,
)

# Free-text fragments that map onto a structured preference. Kept deliberately
# short: these two are unambiguous and the intake form's own placeholder text
# suggests them ("Direct flights, local food, quiet hotel"). Anything subtler
# ("I have a 2pm meeting" -> a hard ArrivalPreference) needs a real intake
# parsing step, which is upstream of this agent by design — see the
# FlightPreferences docstring. Guessing at it here would turn a soft
# preference into a hard filter on the strength of a substring match.
_DIRECT_HINTS = ("direct flight", "direct flights", "non-stop", "nonstop", "no layover")
_RED_EYE_HINTS = ("no red-eye", "no red eye", "avoid red-eye", "avoid red eye", "no overnight flight")

# Substrings that mean a traveller needs wheelchair assistance. The form takes
# free text ("wheelchair assistance", "wheelchair user"), so an exact match on
# the bare word would miss almost every real entry.
_WHEELCHAIR_HINTS = ("wheelchair",)


@dataclass
class AdaptedRequest:
    """A `FlightProposalRequest` plus what could not be resolved building it."""

    request: FlightProposalRequest
    unresolved: list[str] = field(default_factory=list)

    @property
    def is_routable(self) -> bool:
        """Whether both airports resolved. False means zero candidates, by
        construction — worth checking before spending an LLM call."""
        ctx = self.request.trip_context
        return bool(ctx.origin_airport and ctx.dest_airport)

    @property
    def has_inventory(self) -> bool:
        """Routable AND backed by seed inventory. Distinguishes "cannot route"
        from "can route, no data loaded yet"; both give no candidates, for
        different reasons a traveller deserves to be told apart."""
        ctx = self.request.trip_context
        return self.is_routable and has_seed_inventory(ctx.origin_country) and has_seed_inventory(ctx.dest_country)


def strip_traveller_prefix(need: str) -> str:
    """Remove the `"Traveler 3: "` prefix `app.js` adds to combined needs.

    The form builds its trip-level `accessibility_needs` by prefixing each
    per-traveller entry with the traveller's number. Useful for display,
    but it means a raw entry never equals the need it describes.
    """
    _, separator, remainder = need.partition(":")
    return (remainder if separator else need).strip()


def needs_wheelchair(accessibility_needs: list[str]) -> bool:
    """Whether any stated need indicates wheelchair assistance.

    Substring matching on purpose. The form accepts free text, so real entries
    read "wheelchair assistance" or "Traveler 2: wheelchair user", none of
    which equal the bare token an exact match would look for.
    """
    return any(
        hint in strip_traveller_prefix(need).lower()
        for need in accessibility_needs
        for hint in _WHEELCHAIR_HINTS
    )


def derive_flight_preferences(preferences: list[str]) -> FlightPreferences:
    """Best-effort structured preferences from the form's free-text list.

    Only sets SOFT preferences, and only on unambiguous phrases. A soft
    preference affects ranking and can be relaxed by the option-B path; it
    never excludes a flight. That asymmetry is what makes a conservative guess
    safe here — a wrong guess costs ranking order, not a viable option.
    """
    lowered = [p.lower() for p in preferences]
    joined = " | ".join(lowered)
    return FlightPreferences(
        prefer_direct=any(hint in joined for hint in _DIRECT_HINTS),
        avoid_red_eye=any(hint in joined for hint in _RED_EYE_HINTS),
    )


def to_trip_context(travel_request, *, passport_country: str | None = None) -> tuple[TripContext, list[str]]:
    """Build a `TripContext` from a shared `TravelRequest`.

    Returns the context and a list of human-readable notes about anything that
    could not be resolved, suitable for surfacing as `AgentFinding.warnings`.
    """
    unresolved: list[str] = []

    origin_country = travel_request.origin
    dest_country = travel_request.destination
    origin_airport = resolve_airport(origin_country)
    dest_airport = resolve_airport(dest_country)

    if origin_airport is None:
        unresolved.append(f"No airport mapping for departure country {origin_country!r}")
    elif not has_seed_inventory(origin_country):
        unresolved.append(f"No flight inventory loaded for {origin_country} ({origin_airport})")
    if dest_airport is None:
        unresolved.append(f"No airport mapping for destination country {dest_country!r}")
    elif not has_seed_inventory(dest_country):
        unresolved.append(f"No flight inventory loaded for {dest_country} ({dest_airport})")

    preferences = list(travel_request.preferences)
    needs = [strip_traveller_prefix(need) for need in travel_request.accessibility_needs]

    context = TripContext(
        origin_country=origin_country,
        dest_country=dest_country,
        origin_airport=origin_airport,
        dest_airport=dest_airport,
        depart_date=travel_request.departure_date.isoformat(),
        return_date=travel_request.return_date.isoformat(),
        traveller_ages=list(travel_request.traveller_ages),
        traveller_genders=list(travel_request.traveller_genders),
        budget_total=travel_request.budget,
        currency=travel_request.currency,
        accessibility_needs=needs,
        traveller_accessibility_needs=[list(n) for n in travel_request.traveller_accessibility_needs],
        passport_country=passport_country,
        preferences=preferences,
        refinement_notes=list(travel_request.refinement_notes),
        flight_preferences=derive_flight_preferences(preferences),
    )
    return context, unresolved


def to_flight_request(travel_request, *, passport_country: str | None = None) -> AdaptedRequest:
    """The full adaptation: shared `TravelRequest` -> `FlightProposalRequest`.

    `constraints` and `negotiation_history` stay empty. Both are
    orchestrator-issued mid-negotiation and have no equivalent on a first-round
    user request; the orchestrator supplies them when a renegotiation loop
    exists in `graph.py`.
    """
    context, unresolved = to_trip_context(travel_request, passport_country=passport_country)
    return AdaptedRequest(request=FlightProposalRequest(trip_context=context), unresolved=unresolved)
