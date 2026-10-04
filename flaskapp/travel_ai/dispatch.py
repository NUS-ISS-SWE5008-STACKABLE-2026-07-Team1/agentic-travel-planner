"""Which specialists a request needs.

Separate from `graph.py` on purpose. Composing the graph is plumbing; deciding
who gets asked is policy, and policy that a traveller can feel should be one
pure function — a request in, a tuple of agent names out — testable without
building a graph or touching a model.

Two rules are not the traveller's to set, and neither is politeness:

`risk_advisory_agent` runs for every scope. Visa and entry rules, local laws
and seasonal disruption are not something to drop silently, and a hotel-only
stay still happens somewhere that has all three.

Hotel-only was once excluded here, on the grounds that the agent reasons about
entry FROM a departure country that this scope does not collect. That was a
claim about the implementation, and it was wrong: `RiskProposalRequest` carries
`destination_slug`, `destination` and the dates, and no origin field at all.
The agent is keyed on where you are going, never on where you set off from, so
a missing origin costs it nothing. `test_risk_advisory_agent.py::
test_the_grounded_path_needs_no_origin` pins that, so the exclusion cannot be
reintroduced on the strength of the same wrong reason.

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
    # A stay rather than a journey, but the advisory still applies: the
    # traveller is somewhere with entry rules, local laws and a typhoon season,
    # whether or not they asked us to book the flight that gets them there.
    "hotel": frozenset({HOTEL, RISK}),
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
