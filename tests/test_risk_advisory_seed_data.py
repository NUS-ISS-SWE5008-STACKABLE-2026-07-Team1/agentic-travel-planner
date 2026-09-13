"""Regression tests against the real `seed_data.py` content — not the small
controlled fixtures `test_risk_advisory_domain.py` uses.

These exist specifically to guard the demo cases: `seed_data.py` deliberately
overlaps a seasonal window with a dated event for Tokyo and for Washington,
so `reasoning.py` has a real, data-backed case to connect two risks into one
insight. A future edit to `seed_data.py`'s dates could silently break that
overlap; these tests catch it.
"""

from __future__ import annotations

from datetime import date

import pytest

from flaskapp.database import initialize, seed_risk_reference_data
from flaskapp.travel_ai.agents.risk_advisory_agent.domain import propose_risks
from flaskapp.travel_ai.agents.risk_advisory_agent.providers.database import DatabaseRiskProvider
from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskProposalRequest
from flaskapp.travel_ai.agents.risk_advisory_agent.seed_data import (
    DATED_EVENTS,
    SEASONAL_WINDOWS,
    STANDING_FACTS,
)

CITIES = ("sg-singapore", "de-berlin", "jp-tokyo", "es-barcelona", "us-washington")


@pytest.fixture(scope="module")
def provider(tmp_path_factory):
    path = tmp_path_factory.mktemp("risk-seed") / "seed.sqlite3"
    initialize(path)
    seed_risk_reference_data(path, STANDING_FACTS, SEASONAL_WINDOWS, DATED_EVENTS)
    return DatabaseRiskProvider(path)


def _request(slug: str, departure: date, return_: date) -> RiskProposalRequest:
    return RiskProposalRequest(destination_slug=slug, destination=slug, departure_date=departure, return_date=return_)


@pytest.mark.parametrize("slug", CITIES)
def test_every_seeded_city_is_covered_and_non_trivial(provider, slug):
    request = _request(slug, date(2026, 1, 10), date(2026, 1, 15))
    assert provider.covers(request)
    proposal = propose_risks(request, provider)
    assert len(proposal.items) >= 10


def test_tokyo_august_trip_surfaces_the_deliberate_triple_overlap(provider):
    """Typhoon season, summer heat, and the Obon holiday all fall in this
    window on purpose — the case `reasoning.py`'s cross-referencing exists
    to demonstrate."""
    proposal = propose_risks(_request("jp-tokyo", date(2026, 8, 12), date(2026, 8, 17)), provider)
    titles = {item.title for item in proposal.items}
    assert {"Typhoon season", "Summer heat and humidity", "Obon holiday period"} <= titles


def test_washington_july_trip_surfaces_the_deliberate_overlap(provider):
    """Independence Day falls inside the hurricane-remnant season on purpose."""
    proposal = propose_risks(_request("us-washington", date(2026, 7, 2), date(2026, 7, 6)), provider)
    titles = {item.title for item in proposal.items}
    assert {"Atlantic hurricane season (remnants)", "Independence Day (Fourth of July)"} <= titles


def test_risk_ids_are_unique_within_a_single_proposal(provider):
    """A duplicate id would make `validate_grounded_response` unable to tell
    two different facts apart."""
    for slug in CITIES:
        proposal = propose_risks(_request(slug, date(2026, 1, 1), date(2026, 12, 31)), provider)
        ids = [item.risk_id for item in proposal.items]
        assert len(ids) == len(set(ids)), f"duplicate risk_id for {slug}"
