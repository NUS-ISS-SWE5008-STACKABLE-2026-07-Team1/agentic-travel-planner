"""Bias-audit invariants for the XRAI work.

`traveller_genders` is deliberately carried into the model's context so an
audit can vary it — see `TripContext`'s docstring. That only produces a usable
finding if the deterministic half is provably gender-blind first: otherwise a
difference in output could come from either layer and the audit proves nothing.

These tests pin that down. Ranking must be byte-identical across genders, so
any difference an audit observes is attributable to the LLM alone.
"""

from datetime import date

import pytest

from flaskapp.travel_ai.agents.flight_agent.adapter import to_flight_request
from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights, screen_flights
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY
from flaskapp.travel_ai.schemas import TravelRequest

GENDERS = ["female", "male", "non_binary", "prefer_not_to_say"]

BASE = dict(
    origin="Singapore", destination="Japan",
    departure_date=date(2026, 9, 1), return_date=date(2026, 9, 5),
    travellers=1, traveller_ages=[34],
    traveller_accessibility_needs=[[]],
    budget=4000, currency="SGD",
    preferences=["direct flights"], accessibility_needs=[],
)


def _proposal_for(gender: str):
    request = TravelRequest(**BASE, traveller_genders=[gender])
    return propose_flights(to_flight_request(request).request, SEED_FLIGHT_INVENTORY)


@pytest.mark.parametrize("gender", GENDERS)
def test_ranking_is_identical_across_genders(gender):
    """The control for the whole audit: if this ever fails, a bias finding
    cannot be attributed to the model."""
    baseline = [c.model_dump() for c in _proposal_for("female").candidates]
    assert [c.model_dump() for c in _proposal_for(gender).candidates] == baseline


@pytest.mark.parametrize("gender", GENDERS)
def test_screening_reasons_are_identical_across_genders(gender):
    """Rejection reasons feed the explainability panel, so they must be
    gender-blind too — not just the survivors."""
    baseline = [s.model_dump() for s in screen_flights(
        to_flight_request(TravelRequest(**BASE, traveller_genders=["female"])).request,
        SEED_FLIGHT_INVENTORY,
    )]
    actual = [s.model_dump() for s in screen_flights(
        to_flight_request(TravelRequest(**BASE, traveller_genders=[gender])).request,
        SEED_FLIGHT_INVENTORY,
    )]
    assert actual == baseline


def test_gender_reaches_the_model_context():
    """The audit needs the model to actually see the attribute; a test that
    the field survives the adapter is what stops a future cleanup from
    silently disabling the experiment."""
    context = to_flight_request(
        TravelRequest(**BASE, traveller_genders=["non_binary"])
    ).request.trip_context
    assert context.traveller_genders == ["non_binary"]
    assert "traveller_genders" in context.model_dump()


def test_domain_module_never_reads_gender():
    """Structural guard: ranking code must not reference the attribute at all,
    which is stronger than observing equal outputs on a few fixtures."""
    from pathlib import Path

    from flaskapp.travel_ai.agents.flight_agent import domain

    source = Path(domain.__file__).read_text(encoding="utf-8")
    assert "gender" not in source.lower()
