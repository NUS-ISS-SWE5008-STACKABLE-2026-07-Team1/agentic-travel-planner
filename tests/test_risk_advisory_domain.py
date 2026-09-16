"""domain.py's query/date-filter logic, against a small controlled dataset —
not the full seed_data.py content, so a change to the real reference data
can't silently break what this file actually verifies."""

from __future__ import annotations

from datetime import date

import pytest

from flaskapp.travel_ai.agents.risk_advisory_agent.domain import propose_risks
from flaskapp.travel_ai.agents.risk_advisory_agent.providers.base import RiskFetchResult
from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskProposalRequest

STANDING = [
    {"destination_slug": "zz-testland", "category": "visa_entry", "severity": "low",
     "title": "Visa-free", "detail": "90 days visa-free.", "mitigation": None},
    {"destination_slug": "zz-testland", "category": "crime_safety", "severity": "medium",
     "title": "Petty theft", "detail": "Occurs in tourist areas.", "mitigation": "Stay alert."},
]
SEASONAL = [
    # Wraps the year end: Nov (11) through Feb (2).
    {"destination_slug": "zz-testland", "category": "seasonal_weather", "label": "Winter storms",
     "start_month": 11, "end_month": 2, "severity": "medium", "detail": "Storms disrupt travel."},
    {"destination_slug": "zz-testland", "category": "seasonal_weather", "label": "Monsoon",
     "start_month": 6, "end_month": 8, "severity": "high", "detail": "Heavy flooding risk."},
]
DATED = [
    {"destination_slug": "zz-testland", "category": "local_event", "name": "Founders Festival",
     "start_date": "2026-07-10", "end_date": "2026-07-14", "impact": "price_surge", "detail": "City-wide festival."},
]


class FakeProvider:
    """A `RiskDataProvider` over a plain in-memory list — same shape as
    `SeedRiskProvider`, minus the CSV file, so this file tests `domain.py`'s
    query/date logic without depending on the real `seed_data.py` content."""

    def __init__(self, standing=STANDING, seasonal=SEASONAL, dated=DATED):
        self._standing = standing
        self._seasonal = seasonal
        self._dated = dated

    def covers(self, request: RiskProposalRequest) -> bool:
        return any(f["destination_slug"] == request.destination_slug for f in self._standing)

    def fetch(self, request: RiskProposalRequest) -> RiskFetchResult:
        slug = request.destination_slug
        return RiskFetchResult(
            standing_facts=[f for f in self._standing if f["destination_slug"] == slug],
            seasonal_windows=[w for w in self._seasonal if w["destination_slug"] == slug],
            dated_events=[e for e in self._dated if e["destination_slug"] == slug],
        )


@pytest.fixture
def provider():
    return FakeProvider()


def _request(departure: date, return_: date, slug="zz-testland") -> RiskProposalRequest:
    return RiskProposalRequest(
        destination_slug=slug, destination="Testland", departure_date=departure, return_date=return_,
    )


def test_standing_facts_always_included(provider):
    proposal = propose_risks(_request(date(2026, 1, 1), date(2026, 1, 5)), provider)
    categories = {item.category for item in proposal.items if item.kind == "standing_fact"}
    assert categories == {"visa_entry", "crime_safety"}


def test_seasonal_window_included_when_trip_overlaps_it(provider):
    # July is inside the monsoon window (6-8), not the winter one.
    proposal = propose_risks(_request(date(2026, 7, 1), date(2026, 7, 5)), provider)
    labels = {item.title for item in proposal.items if item.kind == "seasonal_window"}
    assert labels == {"Monsoon"}


def test_seasonal_window_excluded_when_trip_does_not_overlap(provider):
    proposal = propose_risks(_request(date(2026, 4, 1), date(2026, 4, 5)), provider)
    assert not [item for item in proposal.items if item.kind == "seasonal_window"]


def test_seasonal_window_wrapping_year_end_matches_january(provider):
    proposal = propose_risks(_request(date(2026, 1, 10), date(2026, 1, 15)), provider)
    labels = {item.title for item in proposal.items if item.kind == "seasonal_window"}
    assert labels == {"Winter storms"}


def test_dated_event_included_only_when_trip_overlaps_its_range(provider):
    overlapping = propose_risks(_request(date(2026, 7, 12), date(2026, 7, 16)), provider)
    assert any(item.title == "Founders Festival" for item in overlapping.items)

    non_overlapping = propose_risks(_request(date(2026, 8, 1), date(2026, 8, 5)), provider)
    assert not any(item.title == "Founders Festival" for item in non_overlapping.items)


def test_compound_case_seasonal_and_event_both_present(provider):
    """The case the design exists for: two independently-grounded facts in
    the same window, available together for reasoning.py to connect."""
    proposal = propose_risks(_request(date(2026, 7, 10), date(2026, 7, 14)), provider)
    kinds_present = {item.kind for item in proposal.items}
    assert kinds_present == {"standing_fact", "seasonal_window", "dated_event"}


def test_items_are_sorted_high_severity_first(provider):
    proposal = propose_risks(_request(date(2026, 7, 10), date(2026, 7, 14)), provider)
    severities = [item.severity for item in proposal.items if item.severity]
    assert severities == sorted(severities, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s])


def test_risk_ids_are_kind_prefixed_and_content_derived(provider):
    proposal = propose_risks(_request(date(2026, 7, 10), date(2026, 7, 14)), provider)
    ids = {item.title: item.risk_id for item in proposal.items}
    assert ids["Petty theft"] == "fact-zz-testland-crime-safety"
    assert ids["Monsoon"] == "season-zz-testland-monsoon"
    assert ids["Founders Festival"] == "event-zz-testland-founders-festival"


def test_risk_ids_are_independent_of_row_order():
    """The point of a content-derived id: nothing about it depends on a
    row's position in the source list, unlike a positional or autoincrement
    id would — inserting a new row anywhere in the CSV must not change what
    an existing risk_id means."""
    reordered = FakeProvider(standing=list(reversed(STANDING)))
    before = propose_risks(_request(date(2026, 1, 1), date(2026, 1, 5)), FakeProvider())
    after = propose_risks(_request(date(2026, 1, 1), date(2026, 1, 5)), reordered)
    assert {item.risk_id for item in before.items} == {item.risk_id for item in after.items}


def test_no_reference_data_for_unmapped_destination(provider):
    proposal = propose_risks(_request(date(2026, 7, 1), date(2026, 7, 5), slug="unknown-slug"), provider)
    assert proposal.items == []


def test_provider_covers_only_known_destinations(provider):
    assert provider.covers(_request(date(2026, 1, 1), date(2026, 1, 2))) is True
    assert provider.covers(_request(date(2026, 1, 1), date(2026, 1, 2), slug="unknown-slug")) is False
    assert provider.covers(_request(date(2026, 1, 1), date(2026, 1, 2), slug=None)) is False
