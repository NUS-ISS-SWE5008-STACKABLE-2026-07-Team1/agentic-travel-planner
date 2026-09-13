"""Composing the graph from a selected set of specialists.

The barrier edge joins on a FIXED source list, and a join whose sources never
execute does not fire. Composing from the selected set keeps that correct by
construction rather than routing around nodes that are still wired in.
"""

from flaskapp.travel_ai.graph import SPECIALISTS, build_travel_graph
from flaskapp.travel_ai.tracing import AuditTracer


class StubLlm:
    """Never invoked: these tests inspect graph shape, not agent behaviour."""

    def with_structured_output(self, *_args, **_kwargs):
        return self


def graph_nodes(tmp_path, specialists=None):
    tracer = AuditTracer(tmp_path, "11111111-1111-4111-8111-111111111111", None)
    kwargs = {} if specialists is None else {"specialists": specialists}
    compiled = build_travel_graph(StubLlm(), tracer, **kwargs)
    return set(compiled.get_graph().nodes)


def test_the_default_still_builds_every_specialist(tmp_path):
    """No caller passing `specialists` may see any change."""
    nodes = graph_nodes(tmp_path)
    assert set(SPECIALISTS) <= nodes
    assert "orchestrator_agent" in nodes


def test_a_restricted_set_omits_the_excluded_node(tmp_path):
    nodes = graph_nodes(tmp_path, ("hotel_transport_agent", "risk_advisory_agent"))
    assert "flight_agent" not in nodes
    assert "hotel_transport_agent" in nodes


def test_the_orchestrator_is_still_reached(tmp_path):
    """The barrier must still have sources, or synthesis never runs."""
    compiled = build_travel_graph(
        StubLlm(), AuditTracer(tmp_path, "11111111-1111-4111-8111-111111111111", None),
        specialists=("risk_advisory_agent",),
    )
    graph = compiled.get_graph()
    assert "orchestrator_agent" in set(graph.nodes)
    targets = {edge.target for edge in graph.edges if edge.source == "risk_advisory_agent"}
    assert "orchestrator_agent" in targets
