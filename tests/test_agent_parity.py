"""Flight and Hotel are near-duplicates: hold their shared guarantees together.

`docs/progress.md` records that the toxicity word-boundary fix "had to be applied
twice" because these two modules are line-for-line copies of each other and only
one was noticed. The Path 2 fabrication fix has exactly the same shape and the
same failure mode, so it gets an executable pairing rather than a comment asking
the next person to remember.

These tests assert the *contract* both agents' prompt-only fallback must satisfy,
not the wording of either. Adding a third grounded agent means adding it to
`PROMPT_ONLY_AGENTS` — at which point these tests start guarding it too.
"""

import inspect

import pytest

from flaskapp.travel_ai.agents.flight_agent import agent as flight_agent
from flaskapp.travel_ai.agents.hotel_transport_agent import agent as hotel_agent
from flaskapp.travel_ai.safeguards import UNVERIFIED_OPTIONS_MARKER
from flaskapp.travel_ai.schemas import AgentFinding, Option
from flaskapp.travel_ai.tracing import AuditTracer

# Every agent that falls back to a prompt-only node when its inventory cannot
# cover the request.
PROMPT_ONLY_AGENTS = [
    pytest.param(flight_agent, id="flight_agent"),
    pytest.param(hotel_agent, id="hotel_transport_agent"),
]


@pytest.fixture
def tracer(tmp_path):
    return AuditTracer(tmp_path, "req-parity")


def _finding(module, **overrides) -> AgentFinding:
    return AgentFinding(
        **{
            "agent": module.NAME,
            "summary": "General guidance for this destination.",
            "options": [],
            "warnings": [],
            "confidence": 0.5,
            **overrides,
        }
    )


@pytest.mark.parametrize("module", PROMPT_ONLY_AGENTS)
def test_prompt_only_path_strips_concrete_options(module, tracer):
    """Neither agent may return a concrete, bookable-looking option from a path
    that has no inventory behind it."""
    postprocess = module._forbid_concrete_options(module.NAME, tracer)
    finding = postprocess(_finding(module, options=[
        Option(name="INVENTED-1", description="Departs 09:15, 420 per night."),
        Option(name="INVENTED-2", description="Second fabrication."),
    ]))

    assert finding.options == []
    assert module.NO_CONCRETE_OPTIONS_WARNING in finding.warnings


@pytest.mark.parametrize("module", PROMPT_ONLY_AGENTS)
def test_prompt_only_path_keeps_the_provenance_marker(module, tracer):
    """`enforce_provenance_disclosure` finds unverified agents by substring-matching
    `UNVERIFIED_OPTIONS_MARKER` against warnings. Reword either ESTIMATE_WARNING
    and the plan-level disclosure silently stops firing for that agent."""
    assert UNVERIFIED_OPTIONS_MARKER in module.ESTIMATE_WARNING


@pytest.mark.parametrize("module", PROMPT_ONLY_AGENTS)
def test_prompt_only_path_screens_output_before_stripping(module, tracer):
    """Order matters: prose failing L1 screening is replaced wholesale, rather
    than having its options stripped and its flagged text kept."""
    postprocess = module._forbid_concrete_options(module.NAME, tracer)
    finding = postprocess(_finding(
        module,
        summary="All women are naturally worse at this, so avoid it.",
        options=[Option(name="INVENTED-1", description="x")],
    ))

    assert "naturally worse" not in finding.summary
    assert finding.confidence == 0.0
    assert finding.options == []


@pytest.mark.parametrize("module", PROMPT_ONLY_AGENTS)
def test_prompt_only_node_is_wired_with_both_hooks(module):
    """The gap this whole workstream closed was not a missing hook — it was two
    hooks that existed and were never passed. Read the source of `create_node`,
    because that omission is invisible from the outside: an unwired node behaves
    identically until someone sends it text that should have been blocked.
    """
    source = inspect.getsource(module.create_node)
    assert "preflight=" in source, f"{module.NAME} prompt-only node has no input screening"
    assert "postprocess=" in source, f"{module.NAME} prompt-only node has no output screening"
    assert "PATH2_INSTRUCTION" in source, (
        f"{module.NAME} fallback node is not using its Path 2 prompt"
    )


@pytest.mark.parametrize("module", PROMPT_ONLY_AGENTS)
def test_strip_traces_a_count_not_the_invented_names(module, tracer):
    """`tracing.py`: details are counts, not content. An invented flight number or
    hotel name must not enter the audit trail."""
    postprocess = module._forbid_concrete_options(module.NAME, tracer)
    postprocess(_finding(module, options=[Option(name="INVENTED-1", description="x")]))

    raw = tracer.path.read_text()
    assert "agent_path2_options_stripped" in raw
    assert "INVENTED-1" not in raw
