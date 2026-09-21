# ADR 0008: Route the reasoning strategy, not the model

**Status:** Accepted — `FLIGHT_AGENT_MODE=auto` is the default

## Context

"Add an LLM router" usually means: classify the incoming request, then send easy
ones to a cheap model and hard ones to an expensive one.

The Flight Agent has two reasoning paths with genuinely different cost profiles:
a single-shot structured call, and a bounded tool-calling loop that can re-search
shifted dates and alternate airports.

## Options

1. **Dynamic model router** — an LLM classifies each request and picks a model.
2. **Route the reasoning strategy** on a deterministic precondition.
3. **Always use the expensive path.**

## Decision

Option 2. `auto` runs single-shot, and escalates to the loop **only when the
deterministic search leaves a leg with no candidates** (`_has_empty_leg`).

The escalation test is pure Python over rows already in memory. It costs
nothing, cannot itself fail, and adds no latency to the common path.

### The measurement that decided it

From `docs/flight_agent/mode-eval.md`, 12 runs per mode against `gpt-4o`:

| mode | median | runs with options |
|---|---|---|
| structured | 5377ms | 8/12 |
| **auto** | **6046ms** | **10/12** |
| agentic | 8525ms | 10/12 |

`auto` matches the always-loop mode's success rate at **71% of its latency**.
The `awkward_dates` scenario went from 0 options to 6. Every other scenario
produced identical option counts.

Option 1 was rejected on three grounds: it puts a classification LLM call on the
critical path, so it adds latency and a new failure mode to *every* request; it
optimises spend at a scale that does not exist; and a model-chosen route is
non-reproducible, which breaks the golden suite and the bias audit.

## Consequences

- **This is a router**, and it is the honest answer to "did you consider one" —
  it routes on a free, deterministic, reproducible signal rather than a
  probabilistic one, and the payoff is measured rather than assumed.
- Escalation is bounded by `LoopBudget`: model turns, tool calls, provider
  calls, wall clock (`FLIGHT_AGENT_MAX_LLM_TURNS`, `MAX_TOOL_CALLS`,
  `MAX_PROVIDER_CALLS`, `LOOP_DEADLINE_SECONDS`).
- `structured` remains the one-variable revert if the loop ever misbehaves.
- The precondition is Flight-specific. Hotel & Transport has the same shape
  available (empty candidate list); Risk & Advisory has no cheap precondition
  at all and would need to always-loop under budget.

**Revisit when:** per-request model cost becomes a tracked budget line, or a
second model tier is introduced whose quality difference is measured rather
than assumed.
