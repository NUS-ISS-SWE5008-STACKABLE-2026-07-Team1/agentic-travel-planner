"""Privacy-preserving requirement extraction and bounded search planning."""

from __future__ import annotations

import re

from flaskapp.travel_ai.agents.accessibility_agent.models import (
    AccessibilityRequirement, AccessibilitySearchPlan,
)
from flaskapp.travel_ai.schemas import TravelGraphState


CATEGORY_TERMS = {
    "mobility": ("wheelchair", "step-free", "step free", "walking", "mobility", "walker", "cane", "transfer", "roll-in", "roll in"),
    "vision": ("blind", "low vision", "braille", "visual", "screen reader"),
    "hearing": ("deaf", "hard of hearing", "hearing", "caption", "sign language", "induction loop"),
    "cognitive": ("cognitive", "neurodiv", "autis", "sensory", "quiet", "simple instructions"),
    "service_animal": ("service animal", "guide dog", "assistance dog"),
    "medical_equipment": ("medical equipment", "oxygen", "cpap", "battery", "ventilator", "dialysis"),
    "dietary": ("allerg", "diet", "gluten", "halal", "kosher", "vegan"),
}

# Search phrases are deliberately generic. Raw disability/medical free text is
# never sent to the external search provider.
CATEGORY_SEARCH = {
    "mobility": "step-free access wheelchair dimensions lifts accessible toilet",
    "vision": "blind low-vision assistance tactile braille audio description",
    "hearing": "deaf hearing assistance induction loop captions sign language",
    "cognitive": "cognitive accessibility quiet space sensory guide clear signage",
    "service_animal": "service animal policy relief area assistance dog",
    "medical_equipment": "medical equipment policy battery oxygen power supply accessibility",
    "dietary": "official allergen dietary accommodation policy",
    "other": "accessibility services facilities measurements",
}

JOURNEY_SEGMENTS = [
    "arrival and airport", "local transport", "accommodation",
    "activities and public spaces", "departure and connections",
]


def _category(text: str) -> str:
    lowered = text.casefold()
    for category, terms in CATEGORY_TERMS.items():
        if any(term in lowered for term in terms):
            return category
    return "other"


def _safe_description(text: str) -> str:
    """Retain a short local description without control characters."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()[:240]


def extract_requirements(state: TravelGraphState) -> list[AccessibilityRequirement]:
    request = state.get("request", {})
    requirements: list[AccessibilityRequirement] = []
    seen: set[tuple[int | None, str]] = set()

    per_traveller = request.get("traveller_accessibility_needs") or []
    for traveller_index, needs in enumerate(per_traveller):
        for raw in needs or []:
            description = _safe_description(str(raw))
            key = (traveller_index, description.casefold())
            if description and key not in seen:
                seen.add(key)
                requirements.append(AccessibilityRequirement(
                    requirement_id=f"R{len(requirements) + 1}",
                    traveller_index=traveller_index,
                    category=_category(description),
                    description=description,
                ))

    # Legacy aggregate needs remain supported, but do not duplicate the
    # traveller-specific copy produced by intake.
    for raw in request.get("accessibility_needs") or []:
        description = _safe_description(str(raw))
        normalized = re.sub(r"^travell?er\s+\d+\s*:\s*", "", description, flags=re.I)
        if not normalized or any(normalized.casefold() == key[1] for key in seen):
            continue
        seen.add((None, normalized.casefold()))
        requirements.append(AccessibilityRequirement(
            requirement_id=f"R{len(requirements) + 1}", category=_category(normalized),
            description=normalized,
        ))
    return requirements


def build_search_plan(state: TravelGraphState) -> AccessibilitySearchPlan:
    request = state.get("request", {})
    city = str(request.get("destination_city") or "").strip()
    country = str(request.get("destination") or "destination").strip()
    destination = ", ".join(part for part in (city, country) if part)[:160]
    requirements = extract_requirements(state)
    categories = list(dict.fromkeys(item.category for item in requirements)) or ["other"]
    queries = [
        f"{destination} {CATEGORY_SEARCH[category]} official verified"
        for category in categories[:4]
    ]
    return AccessibilitySearchPlan(
        destination=destination, requirements=requirements,
        journey_segments=JOURNEY_SEGMENTS, queries=queries,
    )
