"""The orchestrator's output gate — the boundary that previously had none.

The specialists screen their own rationales, but their findings are rewritten
here into the prose that actually ships, and that rewrite reached the traveller
unscreened: `assess_plan` runs afterwards and only appends warnings, it never
withholds.

Every test stubs the model. What is being checked is the control flow around it
— retry once, then withhold, and record every verdict — not what a model says.
"""

from __future__ import annotations

import json
import re

import pytest

from flaskapp.travel_ai.agents.orchestrator_agent.agent import NAME, create_node, plan_text
from flaskapp.travel_ai.guardrails.types import Category, Decision, Verdict
from flaskapp.travel_ai.schemas import AgentFinding, TravelPlan
from flaskapp.travel_ai.tracing import AuditTracer, verify_hash_chain

_CANARY_PATTERN = re.compile(r"CANARY_[0-9a-f]+")


class StubLlm:
    """Returns each queued plan in turn, so a retry can differ from the first try."""

    def __init__(self, *plans):
        self.plans = list(plans)
        self.calls = 0

    def with_structured_output(self, _schema, **_kwargs):
        return self

    def invoke(self, _messages, config=None):
        self.calls += 1
        return self.plans[min(self.calls - 1, len(self.plans) - 1)]


class StubGuardrail:
    """Blocks the first `block_times` outputs, then allows."""

    def __init__(self, block_times: int):
        self.block_times = block_times
        self.calls = 0

    def screen_output(self, _text):
        self.calls += 1
        blocking = self.calls <= self.block_times
        return Verdict(
            decision=Decision.BLOCK if blocking else Decision.ALLOW,
            category=Category.BIAS_STEREOTYPING if blocking else Category.NONE,
            confidence=0.9, latency_ms=5,
        )


def a_plan(title="Tokyo in autumn", summary="Seven days in Tokyo.") -> TravelPlan:
    return TravelPlan(
        title=title, summary=summary,
        itinerary=["Day 1: arrive"], rationale=["Cheapest direct option"],
    )


def a_finding() -> AgentFinding:
    return AgentFinding(
        agent="flight_agent", summary="Two direct options under budget.", confidence=0.8
    )


@pytest.fixture
def tracer(tmp_path):
    return AuditTracer(tmp_path, "orchestrator-gate")


def state():
    return {"request_id": "orchestrator-gate", "request": {}, "findings": [a_finding()]}


def events(tracer) -> list[dict]:
    return [
        json.loads(line)
        for line in tracer.path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_a_clean_plan_passes_through_unchanged(tracer):
    llm = StubLlm(a_plan())
    result = create_node(llm, tracer, StubGuardrail(block_times=0))(state())
    assert result["plan"].title == "Tokyo in autumn"
    assert llm.calls == 1


def test_a_blocked_plan_is_retried_once(tracer):
    llm = StubLlm(a_plan(title="biased"), a_plan(title="clean second attempt"))
    result = create_node(llm, tracer, StubGuardrail(block_times=1))(state())
    assert result["plan"].title == "clean second attempt"
    assert llm.calls == 2


def test_two_blocked_attempts_withhold_rather_than_fail(tracer):
    """Raising would surface as a generic failure and lose the specialists' work.

    Withholding keeps the contract the frontend renders and preserves each
    specialist's own summary — those passed their own agents' output gates, so
    they are safe to show even when the synthesis of them was not.
    """
    llm = StubLlm(a_plan(title="biased"))
    result = create_node(llm, tracer, StubGuardrail(block_times=99))(state())
    plan = result["plan"]
    assert llm.calls == 2
    assert plan.title == "Plan withheld by output guardrails"
    assert "withheld" in plan.limitations[0]
    assert plan.rationale == ["flight_agent: Two direct options under budget."]


def test_every_verdict_reaches_the_audit_trail(tracer):
    create_node(StubLlm(a_plan()), tracer, StubGuardrail(block_times=1))(state())
    verdicts = [e for e in events(tracer) if e["event"] == "guardrail_llm_verdict"]
    assert [v["details"]["attempt"] for v in verdicts] == [1, 2]
    assert [v["details"]["decision"] for v in verdicts] == ["block", "allow"]
    assert all(v["details"]["gate"] == "output" for v in verdicts)


def test_withholding_is_recorded_not_silent(tracer):
    create_node(StubLlm(a_plan()), tracer, StubGuardrail(block_times=99))(state())
    assert any(e["event"] == "agent_output_withheld" for e in events(tracer))


def test_the_trace_chain_still_verifies_with_the_new_events(tracer):
    create_node(StubLlm(a_plan()), tracer, StubGuardrail(block_times=1))(state())
    assert verify_hash_chain(tracer.path) is True


def test_no_guardrail_leaves_behaviour_exactly_as_before(tracer):
    """`GUARDRAIL_LLM_ENABLED=false` and every existing test path go through here."""
    llm = StubLlm(a_plan())
    result = create_node(llm, tracer, None)(state())
    assert result["plan"].title == "Tokyo in autumn"
    assert llm.calls == 1
    assert not [e for e in events(tracer) if e["event"] == "guardrail_llm_verdict"]


def test_screened_text_covers_what_a_traveller_reads():
    plan = TravelPlan(
        title="T", summary="S", itinerary=["I"], rationale=["R"],
        alternatives=["A"], assumptions=["AS"], limitations=["L"],
    )
    assert set(plan_text(plan).split("\n")) == {"T", "S", "I", "R", "A", "AS", "L"}


def test_agent_name_is_stamped_on_the_verdict_events(tracer):
    create_node(StubLlm(a_plan()), tracer, StubGuardrail(block_times=0))(state())
    verdicts = [e for e in events(tracer) if e["event"] == "guardrail_llm_verdict"]
    assert verdicts and all(v["agent"] == NAME for v in verdicts)


class LeakyStubLlm:
    """A model that echoes back the canary token it finds in its own system
    prompt, `leak_times` attempts in a row — simulating a model successfully
    manipulated into reciting its instructions. Reads the token out of the
    real embedded system message rather than being handed it directly, so the
    test exercises the real `embed_canary` wiring, not a fake of it."""

    def __init__(self, leak_times: int):
        self.leak_times = leak_times
        self.calls = 0

    def with_structured_output(self, _schema, **_kwargs):
        return self

    def invoke(self, messages, config=None):
        self.calls += 1
        token = _CANARY_PATTERN.search(messages[0].content).group(0)
        if self.calls <= self.leak_times:
            return a_plan(summary=f"Debug trace: {token}")
        return a_plan(title="clean, no leak")


def test_a_leaked_canary_is_retried_then_recovers(tracer):
    llm = LeakyStubLlm(leak_times=1)
    result = create_node(llm, tracer, None)(state())
    assert result["plan"].title == "clean, no leak"
    assert llm.calls == 2


def test_a_canary_leak_on_every_attempt_is_withheld_even_with_no_l2(tracer):
    """The canary is an independent check: it must catch a leak L2 alone would
    miss (no guardrail configured here at all), not merely agree with it."""
    llm = LeakyStubLlm(leak_times=99)
    result = create_node(llm, tracer, None)(state())
    assert llm.calls == 2
    assert result["plan"].title == "Plan withheld by output guardrails"


def test_canary_leak_is_recorded_without_the_token_itself(tracer):
    create_node(LeakyStubLlm(leak_times=99), tracer, None)(state())
    leaks = [e for e in events(tracer) if e["event"] == "guardrail_canary_leak_detected"]
    assert [leak["details"]["attempt"] for leak in leaks] == [1, 2]
    assert all("CANARY" not in json.dumps(leak) for leak in leaks)
