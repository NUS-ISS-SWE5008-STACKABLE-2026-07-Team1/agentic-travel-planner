"""L2: the LLM guardrail classifier.

Runs only after L0 (pydantic) and L1 (regex/keyword) have passed, so it never
pays for text a cheap certain check could reject. What it adds over L1 is
semantic recall: `flight_agent/guardrails.py:53-58` is four literal phrasings,
which catches "ignore previous instructions" and misses the same attack encoded
in base64, spelled in leetspeak, split across whitespace, translated, or wrapped
in role-play. That class of attack is the entire reason this layer exists.

Fail-closed by construction, matching the deterministic layers' posture: any
error, timeout, or unparseable response becomes a BLOCK unless the deployment
has explicitly opted into `GUARDRAIL_FAIL_MODE=open`. The exception type is
carried on the verdict so a failure is diagnosable from the audit trail in
seconds rather than looking like a legitimate block.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from flaskapp.travel_ai.guardrails import cache
from flaskapp.travel_ai.guardrails.prompts import (
    INPUT_CLASSIFIER_SYSTEM, OUTPUT_CLASSIFIER_SYSTEM, wrap_untrusted,
)
from flaskapp.travel_ai.guardrails.schema import GUARDRAIL_PROMPT_VERSION, GuardrailVerdict
from flaskapp.travel_ai.guardrails.types import ALLOWED, Category, Decision, Verdict
from flaskapp.travel_ai.llm import build_llm

# Cap on how much text is sent for judgement. MAX_INPUT_CHARS (12000) already
# bounds a request, but a single classifier call should stay small and fast, and
# an injection that needs more than this to express itself is not a realistic
# threat against a preferences textarea.
MAX_SCREENED_CHARS = 6000


class LlmGuardrail:
    """Screens traveller input and generated output through a small model.

    Construct with `build_guardrail(config)` rather than directly, so the model,
    timeout, threshold and fail mode all come from one place.
    """

    def __init__(
        self, llm_settings: Mapping[str, Any] | None, *, enabled: bool = True,
        threshold: float = 0.7, fail_mode: str = "closed", llm: Any = None,
        output_llm_settings: Mapping[str, Any] | None = None,
    ):
        # One settings dict per gate. `output_llm_settings=None` means "same as
        # input", which keeps the single-model deployment a one-liner and keeps
        # every existing caller working.
        output_source = llm_settings if output_llm_settings is None else output_llm_settings
        self._gate_settings = {
            "input": dict(llm_settings or {}),
            "output": dict(output_source or {}),
        }
        self._enabled = enabled
        self._threshold = threshold
        self._fail_mode = fail_mode
        self._llm = llm
        self._structured: dict[str, Any] = {}

    # -- wiring -----------------------------------------------------------

    def _client(self, gate: str):
        """Build the structured client for one gate once, lazily.

        Lazily because a disabled guardrail, or one whose every verdict is a
        cache hit, should not construct a provider client at all — and because
        tests inject `llm=` directly and must never touch the network.

        Per gate because input and output may run different models. An injected
        `llm=` overrides both: a test stub stands in for the whole layer, and
        splitting it in two would silently halve what a stub-based test covers.
        """
        if gate not in self._structured:
            client = (
                self._llm if self._llm is not None
                else build_llm(**self._gate_settings[gate])
            )
            self._structured[gate] = client.with_structured_output(
                GuardrailVerdict, method="json_schema"
            )
        return self._structured[gate]

    def model_for(self, gate: str) -> str | None:
        """Which model this gate will call. Read by the eval report and traces."""
        return self._gate_settings.get(gate, {}).get("model")

    # -- public API -------------------------------------------------------

    def screen_input(self, texts: list[str]) -> Verdict:
        """Judge every piece of traveller free text in a single call."""
        joined = "\n".join(text for text in texts if isinstance(text, str) and text.strip())
        if not joined.strip():
            return ALLOWED
        return self._classify("input", INPUT_CLASSIFIER_SYSTEM, joined)

    def screen_output(self, text: str) -> Verdict:
        """Judge generated prose before it reaches the traveller."""
        if not isinstance(text, str) or not text.strip():
            return ALLOWED
        return self._classify("output", OUTPUT_CLASSIFIER_SYSTEM, text)

    # -- internals --------------------------------------------------------

    def _classify(self, kind: str, system_prompt: str, text: str) -> Verdict:
        if not self._enabled:
            return ALLOWED
        text = text[:MAX_SCREENED_CHARS]
        # The model is part of the key. Two gates now run potentially different
        # models over the same text, and swapping a model must not serve back a
        # verdict the previous one reached — a cached ALLOW from a stronger
        # model would make a weaker replacement look better than it is, which
        # is exactly the comparison the eval harness exists to make.
        key = cache.cache_key(kind, GUARDRAIL_PROMPT_VERSION, self.model_for(kind), text)
        cached = cache.get(key)
        if cached is not None:
            return Verdict(
                decision=cached.decision, category=cached.category,
                confidence=cached.confidence, layer=cached.layer,
                latency_ms=0, rationale=cached.rationale, cache_hit=True,
            )

        started = time.perf_counter()
        try:
            # Untrusted text goes in the HumanMessage, never the SystemMessage.
            # Enforced in CI by .semgrep/llm-agent.yml's
            # llm-untrusted-data-in-system-message rule.
            raw = self._client(kind).invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=wrap_untrusted(text)),
            ])
            verdict = self._to_verdict(raw, self._elapsed_ms(started))
        except Exception as exc:  # noqa: BLE001 - every failure maps to the fail mode
            return self._failure_verdict(exc, self._elapsed_ms(started))

        cache.put(key, verdict)
        return verdict

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _to_verdict(self, raw: Any, latency_ms: int) -> Verdict:
        """Map the model's structured answer onto a Verdict.

        A structured-output call can still return None on some providers when
        the model declines to answer; that is a guardrail failure, not an allow.
        """
        if raw is None:
            raise ValueError("classifier returned no structured verdict")
        confidence = min(1.0, max(0.0, float(raw.confidence)))
        category = Category(raw.category)
        # The threshold is what makes a tuned classifier possible: a "block" the
        # model is not sure about is downgraded to FLAG, which is audited but
        # does not deny the traveller. Tune it from the eval curve, not by feel.
        if raw.decision == "block" and confidence >= self._threshold:
            decision = Decision.BLOCK
        elif raw.decision in {"block", "flag"}:
            decision = Decision.FLAG
        else:
            decision = Decision.ALLOW
        return Verdict(
            decision=decision,
            category=category if decision is not Decision.ALLOW else Category.NONE,
            confidence=confidence,
            layer="L2",
            latency_ms=latency_ms,
            rationale=str(raw.rationale or "")[:200],
        )

    def _failure_verdict(self, exc: Exception, latency_ms: int) -> Verdict:
        """Fail closed unless the deployment explicitly opted out.

        The exception type — not its message, which can carry request content —
        goes into the rationale so an operator can tell a rate limit from a bad
        model name without reading the traveller's text.
        """
        decision = Decision.FLAG if self._fail_mode == "open" else Decision.BLOCK
        return Verdict(
            decision=decision,
            category=Category.OTHER,
            confidence=1.0,
            layer="L2",
            latency_ms=latency_ms,
            rationale=f"guardrail unavailable: {type(exc).__name__}",
        )

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None, llm: Any = None) -> LlmGuardrail:
        """Rebuild from the plain dict `guardrail_settings` produces."""
        settings = dict(settings or {})
        return cls(
            settings.get("llm"),
            enabled=bool(settings.get("enabled", True)),
            threshold=float(settings.get("threshold", 0.7)),
            fail_mode=str(settings.get("fail_mode", "closed")),
            llm=llm,
            output_llm_settings=settings.get("llm_output"),
        )


def guardrail_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten application config into a JSON-shaped guardrail description.

    A plain dict rather than a live object because the classifier has to reach
    the background planning thread the same way every other setting does — via
    the `settings` dict in `jobs.submit_plan` — and because it makes the
    guardrail's whole configuration one inspectable value in tests.

    Produces one settings dict per gate under `llm` (input) and `llm_output`.
    They differ only in model and timeout; provider, credential and endpoint are
    necessarily shared, because a second provider would mean a second credential
    to rotate for no security benefit.

    Note on temperature: the guardrail deliberately does NOT force
    `temperature=0`. Several of the configured default models (the Azure
    `gpt-5` deployment in particular) reject an explicit temperature, and under
    fail-closed that rejection would deny every request in the system. Whatever
    the deployment already uses is dropped rather than overridden; determinism
    is recovered where it matters through the verdict cache and the pinned
    prompt version.
    """
    from flaskapp.config import get_llm_settings

    # `error` is intentionally discarded rather than used to disable the
    # guardrail. Silently switching the classifier off because the provider is
    # misconfigured would be a fail-open path hidden inside a fail-closed
    # design. If settings cannot be built, client construction raises and
    # `_failure_verdict` applies the configured fail mode, which an operator can
    # actually see. In practice the request never gets here with a broken
    # provider — `api.py:55` already returns 503.
    llm_settings, _error = get_llm_settings(config)
    input_settings = output_settings = None
    if llm_settings is not None:
        # Three-step fallback per gate: the gate's own model, then the shared
        # guardrail model, then whatever the planner uses. A deployment that
        # sets nothing behaves exactly as it did before the split.
        shared_model = config.get("GUARDRAIL_LLM_MODEL") or llm_settings.get("model")
        base = {**llm_settings}
        base.pop("temperature", None)
        # `max_retries=0` on both gates, so the configured timeout is the whole
        # budget rather than one attempt of up to three. `build_llm` defaults to
        # 2 retries, which turned the documented 8s input gate into 24s and the
        # 20s output gate into 60s — and under fail-closed that wait is what a
        # traveller sits through before being refused. A transient provider
        # error now fails fast into `_failure_verdict`, which is the decision
        # the fail mode exists to make.
        input_settings = {
            **base,
            "model": config.get("GUARDRAIL_INPUT_LLM_MODEL") or shared_model,
            "timeout": float(config.get("GUARDRAIL_LLM_TIMEOUT_SECONDS", 8)),
            "max_retries": 0,
        }
        output_settings = {
            **base,
            "model": config.get("GUARDRAIL_OUTPUT_LLM_MODEL") or shared_model,
            "timeout": float(config.get("GUARDRAIL_OUTPUT_LLM_TIMEOUT_SECONDS", 20)),
            "max_retries": 0,
        }
    return {
        "llm": input_settings,
        "llm_output": output_settings,
        "enabled": bool(config.get("GUARDRAIL_LLM_ENABLED", True)),
        "threshold": float(config.get("GUARDRAIL_BLOCK_THRESHOLD", 0.7)),
        "fail_mode": str(config.get("GUARDRAIL_FAIL_MODE", "closed")).lower(),
    }


def build_guardrail(config: Mapping[str, Any], llm: Any = None) -> LlmGuardrail:
    """Construct the classifier from application config.

    Mirrors the `vars(Config) if config is None else config` seam that
    `flight_agent/agent.py:190` established, so tests override settings with a
    plain dict and never need environment variables.
    """
    return LlmGuardrail.from_settings(guardrail_settings(config), llm)
