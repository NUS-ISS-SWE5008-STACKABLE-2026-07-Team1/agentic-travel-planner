"""Seed content for Risk & Advisory Agent's reference data, loaded from CSV —
the same pattern Flight (`seed_data_extended.csv`) and Hotel & Transport
(`hotel_seed_data.csv`) already use: plain data files next to the code that
reads them, loaded once into memory at import time, no database involved.

Covers the 5 destinations the team agreed on for demos and testing —
Singapore (SIN), Berlin (BER), Tokyo (HND/NRT), Barcelona (BCN), and
Washington, D.C. (IAD/DCA) — matching `flaskapp/places.py`'s slugs exactly, so
a lookup by the same city a traveller picked in the intake form always hits.

Every fact in the three CSVs started from real government/consular
advisories, safety guides and event calendars (visa rules, local laws, crime
patterns, seasonal weather, named festivals), researched to keep the content
realistic rather than arbitrary. It is nonetheless **illustrative reference
data**, not a live feed — every row's `source` says so (set here, not stored
per-row, since it is the same string for all of them today).

`applies_to` is populated only for `category == "traveler_group_risk"` rows.
Every one of those states a legal or social fact plainly and cites what kind
of fact it is (legal status, general sentiment) rather than characterising a
country or its people — the same attribute-vs-stereotype discipline the
project's own guardrails already enforce elsewhere.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

_SOURCE = "synthetic reference data — illustrative only"
_DIR = Path(__file__).parent


def _optional(value: str | None) -> str | None:
    """CSV has no NULL — an empty field means "not set", not the string ''."""
    return value if value else None


def _load_standing_facts() -> list[dict[str, Any]]:
    with (_DIR / "risk_standing_facts.csv").open(encoding="utf-8", newline="") as f:
        return [
            {
                "destination_slug": row["destination_slug"],
                "category": row["category"],
                "severity": _optional(row["severity"]),
                "applies_to": _optional(row["applies_to"]),
                "title": row["title"],
                "detail": row["detail"],
                "mitigation": _optional(row["mitigation"]),
                "source": _SOURCE,
            }
            for row in csv.DictReader(f)
        ]


def _load_seasonal_windows() -> list[dict[str, Any]]:
    with (_DIR / "risk_seasonal_windows.csv").open(encoding="utf-8", newline="") as f:
        return [
            {
                "destination_slug": row["destination_slug"],
                "category": row["category"],
                "label": row["label"],
                "start_month": int(row["start_month"]),
                "end_month": int(row["end_month"]),
                "severity": row["severity"],
                "detail": row["detail"],
                "mitigation": _optional(row["mitigation"]),
                "source": _SOURCE,
            }
            for row in csv.DictReader(f)
        ]


def _load_dated_events() -> list[dict[str, Any]]:
    with (_DIR / "risk_dated_events.csv").open(encoding="utf-8", newline="") as f:
        return [
            {
                "destination_slug": row["destination_slug"],
                "category": row["category"],
                "name": row["name"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "impact": _optional(row["impact"]),
                "detail": _optional(row["detail"]),
                "source": _SOURCE,
            }
            for row in csv.DictReader(f)
        ]


# Loaded once, at import time. `domain.py` reads these through a
# `RiskDataProvider` (see `providers/seed.py`) — it never imports this module
# directly, which is what lets a future data source (a live feed, say) stand
# in behind the same interface without this file changing.
STANDING_FACTS: list[dict[str, Any]] = _load_standing_facts()
SEASONAL_WINDOWS: list[dict[str, Any]] = _load_seasonal_windows()
DATED_EVENTS: list[dict[str, Any]] = _load_dated_events()
