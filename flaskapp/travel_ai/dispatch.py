"""Which specialists a request needs.

Separate from `graph.py` on purpose. Composing the graph is plumbing; deciding
who gets asked is policy, and policy that a traveller can feel should be one
pure function — a request in, a tuple of agent names out — testable without
building a graph or touching a model.

Two rules are not the traveller's to set, and neither is politeness:

`risk_advisory_agent` runs for any scope that involves a journey. Visa and
entry rules are not something to drop silently. The single exception is
hotel-only, which collects no departure country at all — the agent reasons
about entry FROM somewhere, so running it there would mean advising on a
journey it knows nothing about.

`accessibility_agent` runs whenever any accessibility need is stated, whatever
the scope. `safeguards.assess_plan` warns when needs are present and no
accessibility finding reaches it, so skipping the agent would ship a plan that
ignores a stated requirement and then complains about itself.
"""

from __future__ import annotations

from flaskapp.travel_ai.graph import SPECIALISTS
from flaskapp.travel_ai.schemas import TravelRequest

FLIGHT = "flight_agent"
HOTEL = "hotel_transport_agent"
ACCESSIBILITY = "accessibility_agent"

RISK = "risk_advisory_agent"

# What each scope asks for. `both` means everything, ACCESSIBILITY included:
# it is the default, so it must reproduce today's behaviour exactly for every
# request written before this field existed — stored rows, golden scenarios,
# the bias audit. Narrowing the scope is what makes accessibility conditional,
# and even then a stated need pulls it back in.
_BY_SCOPE: dict[str, frozenset[str]] = {
    "both": frozenset({FLIGHT, HOTEL, ACCESSIBILITY, RISK}),
    "flights": frozenset({FLIGHT, RISK}),
    # A stay, not a journey. Risk & Advisory reasons about visas and entry from
    # the departure country, and a hotel-only request no longer collects one —
    # so it would be advising on a journey it knows nothing about.
    "hotel": frozenset({HOTEL}),
}


def _needs_accessibility(request: TravelRequest) -> bool:
    """Whether the traveller stated an accessibility requirement anywhere."""
    if any(need.strip() for need in request.accessibility_needs):
        return True
    return any(
        need.strip()
        for needs in request.traveller_accessibility_needs
        for need in needs
    )


def specialists_for(request: TravelRequest) -> tuple[str, ...]:
    """The agents to dispatch, in `SPECIALISTS` order.

    Ordered rather than a set so two runs of the same request produce the same
    trace, and so the barrier edge's source list is stable.

    Never empty: `ALWAYS` guarantees at least one source for the barrier edge,
    which means the graph can always reach the orchestrator.

    Takes a validated `TravelRequest`, so an unrecognised `plan_scope` has
    already been rejected at L0 and cannot arrive here. `_BY_SCOPE.get` still
    falls back to the `both` superset rather than raising, because the failure
    that matters is silently dropping a specialist the traveller needed.
    """
    selected = set(_BY_SCOPE.get(request.plan_scope, _BY_SCOPE["both"]))
    if _needs_accessibility(request):
        selected.add(ACCESSIBILITY)
    return tuple(name for name in SPECIALISTS if name in selected)
