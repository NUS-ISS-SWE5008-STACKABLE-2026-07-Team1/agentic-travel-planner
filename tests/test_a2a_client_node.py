"""The orchestrator calling the flight agent over the real A2A protocol.

Both halves are real here: a genuine `build_a2a_application` server and the
official SDK client, talking actual JSON-RPC. Only the network is removed,
via `httpx.ASGITransport` — no ports, no credentials, no live model. That is
what makes the A2A path testable in CI at all, and it is the difference between
"we wrote a client" and "the two ends agree".

The property that matters is the last test: the same request through the
in-process node and through A2A must produce the same finding. If those ever
diverge, the switch is not a transport choice, it is two different products.
"""

from __future__ import annotations

import httpx
import pytest

from flaskapp.config import Config
from flaskapp.travel_ai.a2a_client import RemoteAgentError, create_remote_specialist_node
from flaskapp.travel_ai.a2a_standard import ExecutorContext, build_a2a_application
from flaskapp.travel_ai.agents.flight_agent.agent import NAME, create_node
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightAgentResponse
from flaskapp.travel_ai.tracing import AuditTracer

from tests.test_flight_node import COVERED, StubLLM, _state

ENDPOINT = "http://a2a.test"

NODE_CONFIG = {
    **vars(Config),
    "FLIGHT_AGENT_MODE": "structured",
    "FLIGHT_INVENTORY_SOURCE": "seed",
}


def _grounded_llm():
    return StubLLM(flight_response=FlightAgentResponse(
        rationale="SQ632 arrives earliest.",
        highlighted_flight_ids=[],
        confidence=0.8,
    ))


def _server(tmp_path, database_path=None):
    """A real A2A application serving the real flight node."""
    def node_factory(request_id):
        tracer = AuditTracer(tmp_path, request_id, database_path)
        return create_node(_grounded_llm(), tracer, config=NODE_CONFIG)

    return build_a2a_application(
        {NAME: node_factory},
        ENDPOINT,
        context=ExecutorContext(
            database_path=database_path, trust_caller_request_id=True
        ),
        root_agent=NAME,
    )


def _client_factory(app):
    def factory():
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ENDPOINT
        )

    return factory


def _remote_node(app, tracer, **kwargs):
    return create_remote_specialist_node(
        NAME, tracer, endpoint=ENDPOINT,
        httpx_client_factory=_client_factory(app), **kwargs
    )


@pytest.fixture
def tracer(tmp_path):
    return AuditTracer(tmp_path / "traces", "req-1")


def test_finding_travels_back_over_real_a2a(tmp_path, tracer):
    node = _remote_node(_server(tmp_path), tracer)

    result = node(_state(COVERED))

    finding = result["findings"][0]
    assert finding.agent == NAME
    assert finding.options, "the remote agent returned no flight options"


def test_the_envelope_is_rebuilt_so_the_graph_cannot_tell_the_difference(tmp_path, tracer):
    """`merge_messages` and `save_plan` must not care which transport ran."""
    result = _remote_node(_server(tmp_path), tracer)(_state(COVERED))

    message = result["messages"][0]
    assert message.message_type == "response"
    assert message.sender == NAME
    assert message.recipient == "orchestrator_agent"


def test_the_call_is_traced_at_both_ends(tmp_path, tracer):
    _remote_node(_server(tmp_path), tracer)(_state(COVERED))

    events = [line for line in tracer.path.read_text(encoding="utf-8").splitlines() if line]
    recorded = {__import__("json").loads(line)["event"] for line in events}
    assert "a2a_call_started" in recorded
    assert "a2a_call_completed" in recorded


def test_an_unreachable_agent_fails_rather_than_silently_falling_back(tmp_path, tracer):
    """A silent fallback would make the switch impossible to verify from outside."""
    def broken_factory():
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_a2a_application({}, ENDPOINT)),
            base_url=ENDPOINT,
        )

    node = create_remote_specialist_node(
        NAME, tracer, endpoint=ENDPOINT, httpx_client_factory=broken_factory,
        timeout_seconds=5,
    )

    with pytest.raises(RemoteAgentError):
        node(_state(COVERED))


def test_parent_request_id_is_inherited_so_the_trace_stays_one_chain(tmp_path, tracer):
    """The point of propagation: one audit chain across the A2A hop.

    Without it the specialist opens a second trace under its own task id, and
    the plan's hash chain no longer covers the work done on its behalf.
    """
    traces = tmp_path / "traces"
    app = _server(traces)
    state = _state(COVERED)

    _remote_node(app, tracer)(state)

    parent_trace = traces / f"{state['request_id']}.jsonl"
    assert parent_trace.is_file(), "the specialist did not write into the parent's trace"


def test_both_transports_agree(tmp_path, tracer):
    """Structural equality. Deliberately not asserting on prose.

    The model's wording is non-deterministic on both paths, so comparing
    `summary` would be a test of luck. What must match is everything the
    traveller acts on: which flights, at what price, with what warnings.
    """
    state = _state(COVERED)

    local = create_node(_grounded_llm(), tracer, config=NODE_CONFIG)(state)["findings"][0]
    remote = _remote_node(_server(tmp_path), tracer)(state)["findings"][0]

    assert [o.name for o in remote.options] == [o.name for o in local.options]
    assert [o.estimated_cost for o in remote.options] == pytest.approx(
        [o.estimated_cost for o in local.options]
    )
    assert remote.warnings == local.warnings
    assert remote.agent == local.agent


# --- the switch itself ------------------------------------------------------

def test_transport_defaults_to_inprocess(tracer):
    """The fast path stays the default; A2A is opt-in."""
    from flaskapp.travel_ai.graph import _specialist_node
    from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES

    node = _specialist_node(
        NAME, SPECIALIST_NODE_FACTORIES[NAME], _grounded_llm(), tracer, NODE_CONFIG
    )

    assert node.__name__ == "flight_node"


def test_setting_the_transport_to_a2a_installs_the_remote_node(tracer):
    from flaskapp.travel_ai.graph import _specialist_node
    from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES

    node = _specialist_node(
        NAME, SPECIALIST_NODE_FACTORIES[NAME], _grounded_llm(), tracer,
        {**NODE_CONFIG, "FLIGHT_AGENT_TRANSPORT": "a2a",
         "FLIGHT_AGENT_A2A_URL": ENDPOINT},
    )

    assert node.__name__ == "remote_node"


@pytest.mark.parametrize("name, settings", [
    ("risk_advisory_agent", {"DATABASE": "tmp/e2e.sqlite3"}),
    (NAME, {"DATABASE": "tmp/e2e.sqlite3", "FLIGHT_AGENT_TRANSPORT": "inprocess"}),
])
def test_inprocess_specialists_receive_runtime_settings(name, settings, tracer):
    """Provider-backed agents must read the app's runtime config, not Config defaults."""
    from flaskapp.travel_ai.graph import _specialist_node

    observed = {}

    def fake_factory(_llm, _tracer, config=None):
        observed["config"] = config
        return lambda _state: {}

    _specialist_node(name, fake_factory, _grounded_llm(), tracer, settings)

    assert observed["config"] is settings


def test_the_switch_only_affects_the_flight_agent(tracer):
    """Other specialists keep running in-process regardless."""
    from flaskapp.travel_ai.graph import _specialist_node
    from flaskapp.travel_ai.agents import SPECIALIST_NODE_FACTORIES

    settings = {**NODE_CONFIG, "FLIGHT_AGENT_TRANSPORT": "a2a",
                "FLIGHT_AGENT_A2A_URL": ENDPOINT}
    for name, factory in SPECIALIST_NODE_FACTORIES.items():
        if name == NAME:
            continue
        node = _specialist_node(name, factory, _grounded_llm(), tracer, settings)
        assert node.__name__ != "remote_node", name
