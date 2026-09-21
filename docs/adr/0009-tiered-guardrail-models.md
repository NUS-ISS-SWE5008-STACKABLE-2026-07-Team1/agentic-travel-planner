# ADR 0009: Task-tiered guardrail models, statically bound

**Status:** Accepted — `render.yaml`, `guardrails/classifier.py`

## Context

The L2 guardrail runs two LLM gates with very different constraints.

The **input gate** runs before planning starts, with the traveller waiting, and
the system is fail-closed — so a timeout here refuses a legitimate request.

The **output gate** judges generated prose after ~75 seconds of planning has
already elapsed. Twenty more seconds is proportionally invisible, and the
judgement is subtler: ungrounded claims and bias in fluent text.

## Options

1. **One model for both gates.**
2. **A model per gate, chosen for the gate's constraint.**
3. **Dynamic selection** per request based on content.

## Decision

Option 2, bound in configuration rather than in code:

| Gate | Model | Timeout | Why |
|---|---|---|---|
| Input | `gpt-5-mini` | 8s | Latency-critical; a timeout refuses a real traveller |
| Output | `gpt-5` | 20s | Latency-insensitive; harder judgement |

`classifier.py:240` resolves each gate through a **three-step fallback**: the
gate's own model, then a shared `GUARDRAIL_LLM_MODEL`, then whatever the planner
uses. A deployment that configures nothing behaves exactly as it did before the
per-gate split existed.

## Consequences

- **This is model routing** — static, per-task, with a written rationale. It is
  the second half of the answer to "is there an LLM router" alongside
  [ADR 0008](0008-reasoning-strategy-routing.md).
- Consistent with [ADR 0004](0004-composition-time-binding.md): resolved from
  config, not per request.
- The three-step fallback means a partial configuration degrades to a working
  one instead of crashing.
- A misconfigured provider is **not** allowed to silently disable the guardrail.
  `classifier.py:230` discards the settings error deliberately, so construction
  raises and the configured fail mode applies — a fail-open path hidden inside a
  fail-closed design would be worse than an outage.
- **The two model choices are candidates, not measurements.** `render.yaml:78`
  says so explicitly. `scripts/guardrail_eval.py` exists to settle them and has
  not been run against these models. Until it is, this ADR records a reasoned
  guess, not a result.
- `GUARDRAIL_LLM_ENABLED=false` in deployment pending that live run. The
  deterministic L0/L1 gates are unaffected.

**Revisit when:** `guardrail_eval.py` has been run against both candidates —
then replace the reasoning above with the per-gate numbers.
