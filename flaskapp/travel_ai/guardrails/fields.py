"""The single definition of "which parts of a request are traveller free text".

This existed implicitly in three places and they disagreed. `safeguards.py:69`
screened `preferences + accessibility_needs + refinement_notes`;
`flight_agent/reasoning.py:211` screened preferences and refinement notes;
`accessibility_agent/guardrails.py:34-37` tried to add the city names but read
`origin_place`/`destination_place`, which are not fields on `TravelRequest` —
they are `origin_city`/`destination_city` (`schemas.py:30-31`). That screen
therefore read nothing at all in production, and the test covering it used the
same wrong keys, so nothing caught the divergence.

One collector, used by every caller, is the fix. Adding a free-text field to
`TravelRequest` now means adding it here once.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Request keys holding a list of traveller-authored strings.
FREE_TEXT_LIST_FIELDS = ("preferences", "accessibility_needs", "refinement_notes")

# Request keys holding a single traveller-authored string. City names are user
# input: the intake form posts whatever the traveller typed, and `schemas.py`
# deliberately does not validate them against the places dataset.
FREE_TEXT_SCALAR_FIELDS = ("origin_city", "destination_city")

# Nested one level: one list of needs per traveller.
FREE_TEXT_NESTED_FIELDS = ("traveller_accessibility_needs",)


def collect_free_text(request: Mapping[str, Any] | None) -> list[str]:
    """Every traveller-authored string in a request payload, flattened.

    Accepts the plain-dict form used in graph state as well as
    `TravelRequest.model_dump()`. Non-string entries are skipped rather than
    coerced — numbers and booleans cannot carry an injection, and the request
    shape is not this module's to police (that is L0's job).
    """
    if not isinstance(request, Mapping):
        return []
    texts: list[str] = []
    for field in FREE_TEXT_LIST_FIELDS:
        texts.extend(
            value for value in (request.get(field) or []) if isinstance(value, str)
        )
    for field in FREE_TEXT_SCALAR_FIELDS:
        value = request.get(field)
        if isinstance(value, str):
            texts.append(value)
    for field in FREE_TEXT_NESTED_FIELDS:
        for needs in request.get(field) or []:
            texts.extend(value for value in (needs or []) if isinstance(value, str))
    return [text for text in texts if text.strip()]
