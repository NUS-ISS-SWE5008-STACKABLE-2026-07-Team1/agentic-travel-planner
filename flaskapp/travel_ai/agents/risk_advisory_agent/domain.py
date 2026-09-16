"""Deterministic tool: turns a request into grounded `RiskItem`s. No model call.

Mirrors Flight/Hotel's "the tool decides, the model only narrates" split.
`propose_risks` always runs first and is never bypassed; `reasoning.py` may
only select, prioritise, and connect what this function already found.
"""

from __future__ import annotations

import re
from datetime import date

from flaskapp.travel_ai.agents.risk_advisory_agent.providers.base import (
    RiskDataProvider,
    RiskFetchResult,
)
from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskItem, RiskProposal, RiskProposalRequest

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3}

_NON_WORD = re.compile(r"[^a-z0-9]+")


def _slug(*parts: str) -> str:
    """A stable id component from human-readable text.

    `risk_id` is built from destination and content, not from a row's
    position in the CSV — inserting a new row anywhere in
    `risk_standing_facts.csv` (say) must not change what an existing row's
    id means, or a `risk_id` a trace or audit log already recorded would
    silently point at a different fact after the next edit. A slug of what
    the fact actually is stays the same regardless of row order, which is
    the property an audit trail needs.
    """
    return "-".join(_NON_WORD.sub("-", part.lower()).strip("-") for part in parts)


def _dates_overlap(a_start: str, a_end: str, b_start: date, b_end: date) -> bool:
    """Standard interval-overlap test: two ranges overlap unless one ends
    before the other starts. ISO date strings compare correctly as text, so
    no parsing is needed for the CSV side."""
    return a_start <= str(b_end) and a_end >= str(b_start)


def _months_covered(departure: date, return_: date) -> set[int]:
    """Every calendar month the trip touches, inclusive of both ends."""
    months: set[int] = set()
    year, month = departure.year, departure.month
    while (year, month) <= (return_.year, return_.month):
        months.add(month)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def _window_covers_any(start_month: int, end_month: int, months: set[int]) -> bool:
    """True if the window's month range (which may wrap the year end) touches
    any month the trip is in: (11, 3) means Nov-Mar, stored as-is in the CSV,
    not split into two rows."""
    if start_month <= end_month:
        window_months = set(range(start_month, end_month + 1))
    else:
        window_months = set(range(start_month, 13)) | set(range(1, end_month + 1))
    return bool(window_months & months)


def propose_risks(request: RiskProposalRequest, provider: RiskDataProvider) -> RiskProposal:
    """The tool. Deterministic, no model call: filter by destination, filter
    by date, done.

    Every `RiskItem` traces back to one seed-data row via `risk_id`
    (`fact-<destination>-<category>`, `season-<destination>-<label>`,
    `event-<destination>-<name>`) — that is what
    `reasoning.validate_grounded_response` checks membership against. Sorted
    by severity so the most severe items lead, which matters for the
    fallback path (§ agent.py), where there is no model to do that ordering.
    """
    result: RiskFetchResult = provider.fetch(request)
    slug = request.destination_slug or "unknown"
    items: list[RiskItem] = []

    for row in result.standing_facts:
        items.append(RiskItem(
            risk_id=_slug("fact", slug, row["category"]), kind="standing_fact", category=row["category"],
            severity=row.get("severity"), title=row["title"], detail=row["detail"],
            mitigation=row.get("mitigation"), applies_to=row.get("applies_to"),
            source=row.get("source") or "synthetic reference data — illustrative only",
        ))

    months = _months_covered(request.departure_date, request.return_date)
    for row in result.seasonal_windows:
        if not _window_covers_any(row["start_month"], row["end_month"], months):
            continue
        items.append(RiskItem(
            risk_id=_slug("season", slug, row["label"]), kind="seasonal_window", category=row["category"],
            severity=row.get("severity"), title=row["label"], detail=row["detail"],
            mitigation=row.get("mitigation"),
            source=row.get("source") or "synthetic reference data — illustrative only",
        ))

    for row in result.dated_events:
        if not _dates_overlap(row["start_date"], row["end_date"], request.departure_date, request.return_date):
            continue
        items.append(RiskItem(
            risk_id=_slug("event", slug, row["name"]), kind="dated_event", category=row["category"],
            severity=None, title=row["name"], detail=row.get("detail") or "",
            source=row.get("source") or "synthetic reference data — illustrative only",
        ))

    items.sort(key=lambda item: _SEVERITY_ORDER.get(item.severity, 3))
    return RiskProposal(items=items)
