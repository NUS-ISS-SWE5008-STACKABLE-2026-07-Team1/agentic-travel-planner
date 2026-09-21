"""The input and output gates are separately configurable.

The two gates have opposite requirements. Input runs before anything else with
the traveller waiting, under fail-closed, on adversarial text: it wants a small
fast model whose p95 latency is an availability number. Output runs after ~75
seconds of planning and judges our own model's prose for ungrounded claims and
bias: it can afford nuance, and a timeout there discards a plan five agent calls
just paid for.

These tests exist because the split is invisible at runtime. A guardrail wired
to call one model twice behaves identically to one calling two, right up until
somebody reads the bill or the eval report — so the wiring needs an assertion
rather than a reading.
"""

from __future__ import annotations

import pytest

from flaskapp.travel_ai.guardrails import LlmGuardrail, guardrail_settings
from flaskapp.travel_ai.guardrails import cache, classifier
from flaskapp.travel_ai.guardrails.schema import GuardrailVerdict
from flaskapp.travel_ai.guardrails.types import Decision

BASE_CONFIG = {
    "LLM_PROVIDER": "openai",
    "OPENAI_API_KEY": "test-key-not-real",
    "LLM_MODEL": "planner-model",
}


def config(**overrides) -> dict:
    return {**BASE_CONFIG, **overrides}


# --- what guardrail_settings produces ----------------------------------------


def test_both_gates_inherit_the_planner_model_when_nothing_is_set():
    """A deployment that configures nothing must behave as it did before the split."""
    settings = guardrail_settings(config())
    assert settings["llm"]["model"] == "planner-model"
    assert settings["llm_output"]["model"] == "planner-model"


def test_both_gates_take_the_shared_guardrail_model():
    settings = guardrail_settings(config(GUARDRAIL_LLM_MODEL="small-model"))
    assert settings["llm"]["model"] == "small-model"
    assert settings["llm_output"]["model"] == "small-model"


def test_each_gate_overrides_the_shared_model_independently():
    settings = guardrail_settings(config(
        GUARDRAIL_LLM_MODEL="small-model",
        GUARDRAIL_OUTPUT_LLM_MODEL="nuanced-model",
    ))
    assert settings["llm"]["model"] == "small-model"
    assert settings["llm_output"]["model"] == "nuanced-model"


def test_the_output_gate_carries_its_own_larger_timeout():
    """Independent budgets, not one derived from the other.

    A shared timeout would force the output gate into the input gate's latency
    constraint, which is the constraint the split exists to remove.
    """
    settings = guardrail_settings(config())
    assert settings["llm"]["timeout"] == 8
    assert settings["llm_output"]["timeout"] == 20


def test_raising_the_input_timeout_does_not_raise_the_output_one():
    settings = guardrail_settings(config(GUARDRAIL_LLM_TIMEOUT_SECONDS=30))
    assert settings["llm"]["timeout"] == 30
    assert settings["llm_output"]["timeout"] == 20


def test_temperature_is_dropped_from_both_gates():
    """Several configured defaults reject an explicit temperature.

    Under fail-closed that rejection denies every request, so neither gate may
    carry one — the split must not reintroduce it on the side nobody checked.
    """
    settings = guardrail_settings(config(LLM_TEMPERATURE=0.2))
    assert "temperature" not in settings["llm"]
    assert "temperature" not in settings["llm_output"]


# --- what the classifier actually calls ---------------------------------------


class RecordingLlm:
    """Records the model it was built for, and answers every call the same way."""

    def __init__(self, model):
        self.model = model
        self.calls = 0

    def with_structured_output(self, _schema, **_kwargs):
        return self

    def invoke(self, _messages, **_kwargs):
        self.calls += 1
        return GuardrailVerdict(
            decision="allow", category="none", confidence=0.1, rationale="stub",
        )


@pytest.fixture
def built(monkeypatch):
    """Capture every client the classifier builds, keyed by model name."""
    cache.clear()
    clients: dict[str, RecordingLlm] = {}

    def fake_build_llm(**settings):
        client = RecordingLlm(settings.get("model"))
        clients[client.model] = client
        return client

    monkeypatch.setattr(classifier, "build_llm", fake_build_llm)
    yield clients
    cache.clear()


def test_each_gate_calls_its_own_model(built):
    guard = LlmGuardrail.from_settings(guardrail_settings(config(
        GUARDRAIL_INPUT_LLM_MODEL="fast-model",
        GUARDRAIL_OUTPUT_LLM_MODEL="nuanced-model",
    )))

    assert guard.screen_input(["a trip to Osaka"]).decision is Decision.ALLOW
    assert guard.screen_output("Here is your plan.").decision is Decision.ALLOW

    assert set(built) == {"fast-model", "nuanced-model"}
    assert built["fast-model"].calls == 1
    assert built["nuanced-model"].calls == 1


def test_one_model_is_built_once_and_reused(built):
    """The client is lazy but not rebuilt per call."""
    guard = LlmGuardrail.from_settings(
        guardrail_settings(config(GUARDRAIL_LLM_MODEL="one-model"))
    )
    guard.screen_input(["first"])
    guard.screen_input(["second"])
    assert set(built) == {"one-model"}
    assert built["one-model"].calls == 2


def test_no_client_is_built_when_the_guardrail_is_disabled(built):
    guard = LlmGuardrail.from_settings(guardrail_settings(config(
        GUARDRAIL_LLM_ENABLED=False,
    )))
    guard.screen_input(["anything"])
    guard.screen_output("anything")
    assert built == {}


def test_a_verdict_is_not_reused_across_two_models(built):
    """The cache key carries the model.

    Without this, swapping a model in a long-lived process — or comparing two
    models over one corpus, which is exactly what the eval harness does — serves
    back the previous model's judgement and scores the replacement on work it
    never did.
    """
    text = "identical text judged twice"

    first = LlmGuardrail.from_settings(
        guardrail_settings(config(GUARDRAIL_LLM_MODEL="model-a"))
    )
    first.screen_input([text])

    second = LlmGuardrail.from_settings(
        guardrail_settings(config(GUARDRAIL_LLM_MODEL="model-b"))
    )
    verdict = second.screen_input([text])

    assert built["model-b"].calls == 1, "model-b served a cached verdict from model-a"
    assert verdict.cache_hit is False


def test_the_same_model_still_reuses_a_cached_verdict(built):
    """The model in the key must not defeat the cache it was added to."""
    text = "identical text judged twice"
    guard = LlmGuardrail.from_settings(
        guardrail_settings(config(GUARDRAIL_LLM_MODEL="model-a"))
    )
    guard.screen_input([text])
    second = guard.screen_input([text])

    assert built["model-a"].calls == 1
    assert second.cache_hit is True


def test_an_injected_stub_serves_both_gates(built):
    """`llm=` overrides both gates.

    Splitting it would silently halve what every stub-based test in
    tests/adversarial/ covers, which is most of them.
    """
    stub = RecordingLlm("injected")
    guard = LlmGuardrail.from_settings(
        guardrail_settings(config(
            GUARDRAIL_INPUT_LLM_MODEL="fast-model",
            GUARDRAIL_OUTPUT_LLM_MODEL="nuanced-model",
        )),
        llm=stub,
    )
    guard.screen_input(["one"])
    guard.screen_output("two")

    assert stub.calls == 2
    assert built == {}, "an injected stub must not reach build_llm at all"


# --- the timeout is a budget, not one attempt of several ----------------------


def test_neither_gate_retries_so_its_timeout_is_the_whole_budget():
    """`build_llm` defaults to two retries, which silently tripled both gates:
    the documented 8s input budget became up to 24s and the 20s output budget
    up to 60s. Under fail-closed that is how long a traveller waits before being
    refused, so the gates opt out of retrying."""
    settings = guardrail_settings(config())
    assert settings["llm"]["max_retries"] == 0
    assert settings["llm_output"]["max_retries"] == 0


def test_planning_still_retries():
    """Only the gates opt out. A plan already costs 30-90s, and re-sending a
    transient 429 is worth more than failing the whole run."""
    from flaskapp.travel_ai.llm import build_llm

    planner = build_llm(provider="openai", api_key="test-key-not-real", model="m")
    assert planner.max_retries == 2


def test_build_llm_passes_the_gate_setting_through_to_the_client():
    """The setting is only worth anything if it reaches the client."""
    from flaskapp.travel_ai.llm import build_llm

    gate = build_llm(**{**guardrail_settings(config())["llm_output"], "api_key": "test-key-not-real"})
    assert gate.max_retries == 0
