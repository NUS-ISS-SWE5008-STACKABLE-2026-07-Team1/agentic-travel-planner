"""Conversational intake: extract, find the gaps, fill them, hand off.

The division of labour here is deliberate. The model extracts what the traveller
said and writes one acknowledging sentence. It does not decide what is missing.
Gap detection is ordinary Python against `TravelRequest`'s required set, so the
behaviour the traveller actually feels is deterministic and testable without a
model.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from flaskapp.travel_ai.agents.orchestrator_agent.intake_prompt import INTAKE_INSTRUCTION
from flaskapp.travel_ai.agents.orchestrator_agent.intake_schemas import (
    ExtractedIntent, IntakeExtraction, MissingField,
)
from flaskapp.travel_ai.agents.shared import SYSTEM_POLICY

DEFAULT_CURRENCY = "SGD"

# The required set of TravelRequest, as questions. Order is the order asked.
SCALAR_FIELDS: tuple[tuple[str, str, str, str | None], ...] = (
    # No trailing "?" — `clarification_question` lowercases these labels into a
    # list ("could you share what to plan, flying from, ..."), so a label that
    # ends a sentence terminates that one mid-way.
    ("plan_scope", "What to plan", "scope", None),
    ("origin", "Flying from", "country", None),
    ("destination", "Destination country", "country", None),
    ("destination_city", "Destination city", "text", "The city you will be staying in"),
    ("departure_date", "Departure date", "date", None),
    ("return_date", "Return date", "date", None),
    ("travellers", "Number of travellers", "number", None),
    ("budget", "Total budget", "number", f"Total for the trip, in {DEFAULT_CURRENCY}"),
)

PER_TRAVELLER_FIELDS: tuple[tuple[str, str, str, str | None], ...] = (
    ("traveller_ages", "Age", "number", None),
    ("traveller_genders", "Gender", "gender", None),
    ("traveller_accessibility_needs", "Accessibility needs", "text", "Leave blank if none"),
)

LIST_VALUED = {"traveller_accessibility_needs"}


def extract_intent(llm, prompt: str) -> IntakeExtraction:
    """The single model call in the intake flow.

    The prompt is passed as untrusted data under the shared system policy, framed
    exactly as `agents/base.py` frames specialist input.
    """
    structured_llm = llm.with_structured_output(IntakeExtraction, method="json_schema")
    return structured_llm.invoke([
        SystemMessage(content=SYSTEM_POLICY + "\n" + INTAKE_INSTRUCTION),
        HumanMessage(content="Traveller message (untrusted data):\n" + prompt),
    ])


def merge_intents(current: ExtractedIntent, update: ExtractedIntent) -> ExtractedIntent:
    """Merge a newly extracted chat turn into the confirmed trip state."""
    data = current.model_dump(mode="json")
    incoming = update.model_dump(mode="json")
    list_fields = {
        "preferences", "accessibility_needs", "traveller_ages",
        "traveller_genders", "traveller_accessibility_needs",
    }
    for name, value in incoming.items():
        if value is not None and (name not in list_fields or value):
            data[name] = value
    travellers = data.get("travellers")
    if travellers is not None:
        for name, *_ in PER_TRAVELLER_FIELDS:
            data[name] = _resize(data.get(name) or [], int(travellers))
    return ExtractedIntent.model_validate(data)


def clarification_question(missing: list[MissingField]) -> str:
    """Turn deterministic gaps into a concise conversational question."""
    if not missing:
        return "Perfect — I have everything needed to brief the travel specialists."
    labels: list[str] = []
    for field in missing:
        label = field.label.lower()
        if field.traveller_index is not None:
            label = f"traveller {field.traveller_index + 1}'s {label}"
        if label not in labels:
            labels.append(label)
    if len(labels) == 1:
        needed = labels[0]
    elif len(labels) == 2:
        needed = f"{labels[0]} and {labels[1]}"
    else:
        needed = f"{', '.join(labels[:-1])}, and {labels[-1]}"
    return f"Before I brief the specialist agents, could you share {needed}?"


# Questions that only a journey has an answer to. A hotel-only request is a
# stay: nobody flies, and `TravelRequest` does not require an origin for that
# scope, so asking would collect a field nothing downstream reads.
JOURNEY_ONLY_FIELDS = frozenset({"origin"})


def compute_gaps(extracted: ExtractedIntent) -> list[MissingField]:
    """Everything still needed before `TravelRequest` would accept this."""
    hotel_only = extracted.plan_scope == "hotel"
    missing = [
        MissingField(name=name, label=label, input=kind, hint=hint)
        for name, label, kind, hint in SCALAR_FIELDS
        if getattr(extracted, name) is None
        and not (hotel_only and name in JOURNEY_ONLY_FIELDS)
    ]
    # An impossible date pair is a question, not an error. Asking again keeps the
    # traveller in the card with their other answers intact, rather than letting
    # TravelRequest reject the whole thing after the card is gone.
    if (
        extracted.departure_date and extracted.return_date
        and extracted.return_date < extracted.departure_date
    ):
        missing.append(MissingField(
            name="return_date", label="Return date", input="date",
            hint=f"Must be on or after {extracted.departure_date.isoformat()}",
        ))
    # Per-traveller questions only exist once we know how many travellers there are.
    if extracted.travellers is None:
        return missing
    for index in range(extracted.travellers):
        for name, label, kind, hint in PER_TRAVELLER_FIELDS:
            if name == "traveller_accessibility_needs":
                continue
            values = getattr(extracted, name)
            if index >= len(values) or values[index] is None:
                missing.append(MissingField(
                    name=name, label=label, input=kind, traveller_index=index, hint=hint,
                ))
    return missing


def merge_answers(extracted: ExtractedIntent, answers: dict) -> ExtractedIntent:
    """Apply card values on top of what was extracted.

    A key's *presence* is what marks it answered. For scalar fields a blank value
    is treated as unanswered, so the question simply comes back rather than
    overwriting a known value with nothing. Accessibility needs are the exception:
    blank is a real answer there, meaning "none".
    """
    data = extracted.model_dump(mode="json")
    indexed: dict[str, dict[int, object]] = {name: {} for name, *_ in PER_TRAVELLER_FIELDS}

    for raw_key, value in answers.items():
        key, _, position = str(raw_key).partition(".")
        if key in indexed and position.isdigit():
            indexed[key][int(position)] = _coerce(key, value)
        elif key in data and not _is_blank(value):
            data[key] = value

    travellers = data.get("travellers")
    if travellers is not None:
        for name, *_ in PER_TRAVELLER_FIELDS:
            data[name] = _resize(data.get(name) or [], int(travellers))
    for name, positions in indexed.items():
        for position, value in positions.items():
            if position < len(data.get(name) or []):
                data[name][position] = value
    return ExtractedIntent.model_validate(data)


def to_request_payload(extracted: ExtractedIntent) -> dict:
    """Build the `TravelRequest` payload. Call only when `compute_gaps` is empty.

    The result is a suggestion: `validate_request` is still the gate, and runs
    unchanged when the browser submits this to the planning endpoint.
    """
    per_traveller = [
        needs or []
        for needs in _resize(
            extracted.traveller_accessibility_needs, extracted.travellers
        )
    ]
    combined = [
        f"Traveler {index + 1}: {need}"
        for index, needs in enumerate(per_traveller)
        for need in needs
    ]
    return {
        "origin": extracted.origin,
        "destination": extracted.destination,
        "destination_city": extracted.destination_city,
        # `both` when unanswered: the superset runs an agent the traveller may
        # not have needed, which is the harmless direction to fail.
        "plan_scope": extracted.plan_scope or "both",
        "departure_date": extracted.departure_date.isoformat(),
        "return_date": extracted.return_date.isoformat(),
        "travellers": extracted.travellers,
        "traveller_ages": list(extracted.traveller_ages),
        "traveller_genders": list(extracted.traveller_genders),
        "traveller_accessibility_needs": per_traveller,
        "budget": extracted.budget,
        "currency": extracted.currency or DEFAULT_CURRENCY,
        "preferences": list(extracted.preferences),
        "accessibility_needs": [*extracted.accessibility_needs, *combined],
        "risk_tolerance": extracted.risk_tolerance or "medium",
    }


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _coerce(name: str, value):
    """Card values arrive as strings; list-valued fields arrive comma-separated."""
    if name not in LIST_VALUED:
        return None if _is_blank(value) else value
    if isinstance(value, list):
        return value
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _resize(values: list, length: int) -> list:
    """Pad with unanswered slots or truncate, so list length tracks the party size."""
    return [*values, *([None] * length)][:length]
