"""Translation between the shared `TravelRequest` and Flight Agent's contracts.

This is the seam. `flaskapp/travel_ai/schemas.py` is team-shared and changes as
other agents are developed; Flight Agent's own schemas are tuned to flight
domain logic (legs, airports, seat inventory) and should not be dragged along
with every shared-schema edit. Everything that knows about BOTH lives here, so
a change to either side is a change to one file.

What the shared request cannot supply, and how each gap is handled:

| Needed by Flight Agent | In `TravelRequest`? | Handling |
|---|---|---|
| Airport codes | No — country + city | `airports.resolve_route`, reported when it fails |
| Destination city | Yes, optional | Resolved via `places.py`; falls back to the country's main gateway, disclosed |
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

from flaskapp.travel_ai.agents.flight_agent.airports import resolve_route
from flaskapp.travel_ai.agents.flight_agent.seed_data import covers_route
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
        """Whether both ends resolved to at least one airport. False means zero
        candidates, by construction — worth checking before an LLM call."""
        ctx = self.request.trip_context
        return bool(ctx.origin_airports and ctx.dest_airports)

    @property
    def has_inventory(self) -> bool:
        """Routable AND stocked by the seed dataset. Distinguishes "cannot
        route" from "can route, no data loaded yet"; both give no candidates,
        for different reasons a traveller deserves to be told apart.

        Asks the dataset directly rather than consulting a country allow-list,
        so this stays true as the seed CSV grows.
        """
        ctx = self.request.trip_context
        return self.is_routable and covers_route(ctx.origin_airports, ctx.dest_airports)


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
    preference affects ranking and can be acknowledged as unmet by the
    option-B path; it never excludes a flight. That asymmetry is what makes a
    conservative guess
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
    origin_city = getattr(travel_request, "origin_city", None)
    dest_city = getattr(travel_request, "destination_city", None)

    origin = resolve_route(origin_country, origin_city, label="departure")
    dest = resolve_route(dest_country, dest_city, label="destination")
    unresolved.extend(origin.notes)
    unresolved.extend(dest.notes)

    # Reported separately from resolution failures: a stocked route and an
    # unstocked one are both "no candidates", but only one of them is a
    # coverage gap the traveller can do nothing about.
    if origin.is_resolved and dest.is_resolved and not covers_route(origin.airports, dest.airports):
        unresolved.append(
            f"No flight inventory loaded for {origin.city.name if origin.city else origin_country} "
            f"({'/'.join(origin.airports)}) to "
            f"{dest.city.name if dest.city else dest_country} ({'/'.join(dest.airports)})"
        )

    preferences = list(travel_request.preferences)
    needs = [strip_traveller_prefix(need) for need in travel_request.accessibility_needs]

    context = TripContext(
        origin_country=origin_country,
        dest_country=dest_country,
        # The resolved city, not the raw input: if the traveller sent nothing
        # or something unrecognised, this records what was actually searched.
        origin_city=origin.city.name if origin.city else None,
        dest_city=dest.city.name if dest.city else None,
        origin_airports=list(origin.airports),
        dest_airports=list(dest.airports),
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
