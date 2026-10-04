# ADR 0011: Every failure degrades visibly

**Status:** Accepted — the system's most consistently applied principle

## Context

An agentic system has many partial-failure modes: the model returns malformed
output, a credential is missing, inventory does not cover a route, a budget runs
out, a city is unrecognised, a guardrail cannot reach its provider.

Two bad answers are available for all of them. Fail hard, and one missing API
token takes down a planner whose other four agents were fine. Fail silently, and
the traveller receives a confident plan built on a fallback they were never told
about — which is worse, because it is indistinguishable from a good one.

## Decision

**Every failure degrades to a usable answer, and every degradation is stated in
the output.** Never fail hard, never fail silently.

### The degradation ladder as built

| Failure | Degrades to | Told to the traveller |
|---|---|---|
| Model output malformed or ungrounded | Retry once, then deterministic grounded response | Rationale is plainer; options unchanged |
| Rationale flagged biased or toxic | Same retry-then-fallback | Options unchanged |
| No inventory covers the route | Prompt-only route guidance, **all options stripped** | `ESTIMATE_WARNING` + provenance disclosure |
| Unknown `FLIGHT_INVENTORY_SOURCE` | Seed inventory | Explicit note in the finding |
| Loop budget exhausted | Best candidates found so far | Trace `agent_budget_exhausted` |
| City not in the dataset | Country's primary city | `airports.py:94` note |
| One specialist raises | Other three still synthesise | Finding carries the error |
| Guardrail model unreachable | Gate's model → shared → planner's; then **fail closed** | Request refused, visibly |

### Why the no-inventory path strips options entirely

This is the sharpest instance and the one worth defending. On that path there is
no candidate set, so `validate_grounded_explanation` **cannot run** — the check
that normally makes options trustworthy is structurally unavailable. A
plausible-looking flight is what the UI renders most prominently. So
`_forbid_concrete_options` (`agent.py:120`) removes every option and keeps
route-level guidance plus a warning. Full argument in
`docs/flight_agent/design.md` §3.

### Why the guardrail is the one exception

Everything else degrades *open* — a worse answer beats no answer. The guardrail
degrades **closed**: an unreachable classifier refuses the request. A safety
control that waves traffic through when it breaks is not a safety control.

## Consequences

- No single missing credential can take the planner down.
- `enforce_provenance_disclosure()` exists because a warning **was** paraphrased
  into something milder during orchestrator synthesis, and no unit test caught
  it — each tested one agent's output, never what the orchestrator did with it.
  The safeguard now writes the flag into `plan.limitations` and fails the
  assessment. *The discipline is enforced structurally because prose alone
  demonstrably failed.*
- `ESTIMATE_WARNING` carries the substring `safeguards.UNVERIFIED_OPTIONS_MARKER`
  matches on. **Rewording it silently stops the disclosure firing.**
- Degradation is observable: `agent_fallback`, `agent_path2_options_stripped`,
  `agent_budget_exhausted`, `agent_llm_attempt_failed`, `agent_tool_rejected`.
  Those events are what [ADR 0015](0015-metrics-aggregation.md) proposes to count.
