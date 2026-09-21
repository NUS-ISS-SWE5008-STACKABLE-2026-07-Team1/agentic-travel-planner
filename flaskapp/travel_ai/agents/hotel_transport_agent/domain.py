"""Hotel & Transport Agent domain logic — pure functions, no I/O.

Deliberately decoupled from transport (LangGraph node today, event-bus
consumer later) and from persistence (inventory is passed in as plain objects).
Whatever the team lands on for either, this module should not need to change.
"""

from __future__ import annotations

from collections.abc import Sequence

from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import (
    HotelCandidate,
    HotelInventoryItem,
    HotelPreferences,
    HotelProposal,
    HotelProposalRequest,
    HotelScreeningResult,
    TransportOption,
)

# Substrings that indicate wheelchair assistance needs.
_WHEELCHAIR_HINTS = ("wheelchair",)


def _needs_wheelchair(accessibility_needs: list[str]) -> bool:
    return any(
        hint in need.lower()
        for need in accessibility_needs
        for hint in _WHEELCHAIR_HINTS
    )


def _party_size(ctx) -> int:
    adults = ctx.party.get("adults", 1) or 0
    children = ctx.party.get("children", 0) or 0
    return max(adults + children, 1)


def _matches_city(item: HotelInventoryItem, city_slug: str | None) -> bool:
    if not city_slug:
        return True
    return item.city_slug == city_slug


def _accessibility_failure_reasons(item: HotelInventoryItem, prefs: HotelPreferences) -> list[str]:
    """Hard accessibility failures — filter, don't just rank."""
    reasons: list[str] = []
    if prefs.must_be_accessible:
        if item.wheelchair_accessible is not True:
            reasons.append(
                "wheelchair_accessible is not confirmed"
                if item.wheelchair_accessible is None
                else "wheelchair_accessible is False"
            )
        if item.step_free_entrance is not True:
            reasons.append(
                "step_free_entrance is not confirmed"
                if item.step_free_entrance is None
                else "step_free_entrance is False"
            )
        if item.accessible_bathroom is not True:
            reasons.append(
                "accessible_bathroom is not confirmed"
                if item.accessible_bathroom is None
                else "accessible_bathroom is False"
            )
    return reasons


def _preference_failure_reasons(item: HotelInventoryItem, prefs: HotelPreferences) -> list[str]:
    """Hard preference violations."""
    reasons: list[str] = []
    if prefs.min_star_rating is not None and (item.star_rating or 0) < prefs.min_star_rating:
        reasons.append(
            f"star_rating {item.star_rating} is below minimum {prefs.min_star_rating}"
        )
    if prefs.max_price_per_night is not None and item.price_per_night > prefs.max_price_per_night:
        reasons.append(
            f"price_per_night {item.price_per_night} exceeds max {prefs.max_price_per_night}"
        )
    if prefs.room_type and item.room_type != prefs.room_type:
        reasons.append(
            f"room_type '{item.room_type}' does not match requested '{prefs.room_type}'"
        )
    return reasons


def _screen_item(
    item: HotelInventoryItem,
    request: HotelProposalRequest,
    city_slug: str | None,
) -> list[str]:
    reasons: list[str] = []
    if not _matches_city(item, city_slug):
        reasons.append(f"city_slug '{item.city_slug}' does not match destination")
    prefs = request.trip_context.hotel_preferences
    reasons.extend(_accessibility_failure_reasons(item, prefs))
    reasons.extend(_preference_failure_reasons(item, prefs))
    return reasons


def _rank_key(
    item: HotelInventoryItem,
    prefs: HotelPreferences,
    needs_wheelchair_assist: bool,
) -> tuple:
    """Lexicographic ranking — explainable, not a blended score."""
    return (
        # Unverified accessibility outranks everything for travellers who need it.
        1 if (needs_wheelchair_assist and item.wheelchair_accessible is None) else 0,
        # Hard-constraint failures are ranked first so they sort above viable options.
        0 if not _accessibility_failure_reasons(item, prefs) else 1,
        # Lower price is better (after accessibility).
        item.price_per_night,
        # Higher star rating is better.
        -(item.star_rating or 0),
        # Closer to centre is better.
        item.distance_to_center_km or 9999.0,
    )


def screen_hotels(
    request: HotelProposalRequest,
    inventory: list[HotelInventoryItem],
    city_slug: str | None,
) -> list[HotelScreeningResult]:
    """Post-tool traceability: why every same-city inventory row was or
    wasn't included."""
    results = []
    for item in inventory:
        if item.city_slug != city_slug:
            continue
        reasons = _screen_item(item, request, city_slug)
        results.append(
            HotelScreeningResult(
                hotel_id=item.hotel_id,
                included=not reasons,
                reasons=reasons,
            )
        )
    return results


def _survivors(
    request: HotelProposalRequest,
    inventory: list[HotelInventoryItem],
    city_slug: str | None,
    nights: int,
) -> list[HotelInventoryItem]:
    prefs = request.trip_context.hotel_preferences
    needs_assist = _needs_wheelchair(request.trip_context.accessibility_needs)
    return [
        item
        for item in inventory
        if item.city_slug == city_slug and not _screen_item(item, request, city_slug)
    ]


def propose_hotels(
    request: HotelProposalRequest,
    inventory: list[HotelInventoryItem],
    *,
    top_n: int = 5,
) -> HotelProposal:
    """Rank hotel candidates from `inventory` for one request."""
    ctx = request.trip_context
    city_slug = ctx.dest_city_slug
    nights = ctx.nights
    prefs = ctx.hotel_preferences
    needs_assist = _needs_wheelchair(ctx.accessibility_needs)

    survivors = _survivors(request, inventory, city_slug, nights)
    survivors.sort(
        key=lambda item: _rank_key(item, prefs, needs_assist)
    )

    # All hotel inventory is denominated in SGD; the price figures
    # are SGD regardless of what the request claims. Use SGD here
    # so the currency label always matches the cost. A currency
    # mismatch warning is added by the node when request currency differs.
    hotel_currency = "SGD"
    candidates = [
        HotelCandidate(
            hotel_id=item.hotel_id,
            name=item.name,
            city_slug=item.city_slug,
            star_rating=item.star_rating,
            price_per_night=item.price_per_night,
            room_type=item.room_type,
            distance_to_center_km=item.distance_to_center_km,
            wheelchair_accessible=item.wheelchair_accessible,
            step_free_entrance=item.step_free_entrance,
            accessible_bathroom=item.accessible_bathroom,
            amenities=list(item.amenities),
            source=item.source,
            estimated_total_cost=round(item.price_per_night * nights, 2),
            currency=hotel_currency,
        )
        for item in survivors[:top_n]
    ]
    return HotelProposal(candidates=candidates)


def propose_transport(
    request: HotelProposalRequest,
    transport_options: list[TransportOption],
    *,
    top_n: int = 3,
) -> list[TransportOption]:
    """Rank transport options for one request."""
    ctx = request.trip_context
    needs_assist = _needs_wheelchair(ctx.accessibility_needs)

    ranked = sorted(
        transport_options,
        key=lambda opt: (
            # Confirmed accessible (has notes) ranks first when assist
            # is needed. Unknown (no notes) is treated as neutral —
            # not as inaccessible — since silence is not evidence of
            # a lack of access. If no assist needed, all rank equally.
            0 if (not needs_assist or opt.accessibility_notes) else 1,
            opt.estimated_cost or 999999.0,
            opt.duration_minutes or 999999,
        ),
    )
    return ranked[:top_n]
