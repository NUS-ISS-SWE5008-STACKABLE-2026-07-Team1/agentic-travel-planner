# ADR 0015: Aggregate the trace events into metrics

**Status:** Proposed — closes a gap this project has already documented against itself

## Context

`AuditTracer` records a rich, hash-chained event stream per plan:
`agent_fallback`, `agent_path2_options_stripped`, `agent_llm_attempt_failed`,
`agent_relaxation_applied` / `_rejected`, `agent_tool_rejected`,
`agent_budget_exhausted`, `agent_loop_completed`.

Each is queryable **for one request**, via `GET /api/v1/traces/<request_id>` or
the `audit_events` table. None is counted **across** requests.

The consequence is stated plainly in `docs/individual_reports/flight_agent.md`:

> structured-output validity target ≥95% … is now measurable … but is not yet
> instrumented: `reasoning.py` records a failed attempt in the trace without
> aggregating a rate. **Counting those events against total attempts is the
> small piece of work that turns this from a target into a metric.**

So the project has set quality targets, built the raw signal, and never closed
the loop. A target with no measurement is an intention.

## What "metrics aggregation" means here

Not Prometheus, not a dashboard product. A periodic query over `audit_events`
that turns per-request events into rates, written where the team can see them:

| Metric | From | Target |
|---|---|---|
| Structured-output validity | `1 - (agent_llm_attempt_failed / attempts)` | ≥95% |
| Fallback rate, per agent | `agent_fallback / plans` | trend, not threshold |
| Path 2 rate | `agent_path2_options_stripped / plans` | inventory-coverage proxy |
| Grounding failures | grounding retries / responses | ~0 |
| Relaxation accept rate | `applied / (applied + rejected)` | model-judgement quality |
| Budget exhaustion | `agent_budget_exhausted / loop runs` | budgets too tight if rising |
| Guardrail block rate | verdicts by gate and severity | false-positive watch |
| p50/p95 plan latency | `planning_jobs` timestamps | — |

### Why these are the right metrics

Each one measures a **decision the architecture makes**, not a system resource.
CPU and memory would tell you nothing interesting about this system; the
fallback rate tells you whether [ADR 0011](0011-degrade-visibly.md)'s ladder is
being exercised in production and how often travellers receive a degraded plan
without anyone noticing.

The Path 2 rate is the sharpest: it measures how often the system had **no
inventory** for a request, which is a data-coverage decision surfacing as a
user-visible outcome.

## Decision

Add `scripts/metrics_report.py`, following the established convention of
`flight_mode_eval.py` and `guardrail_eval.py` — a script that queries and emits
markdown into `docs/`. Extend `/admin` to show the same figures live.

Rejected: a metrics backend (Prometheus/Grafana). It needs a scrape endpoint, a
time-series store and a dashboard to answer questions a SQL query over
`audit_events` already answers. The events are durable in the database.

## Consequences

- Three assessed targets stop being aspirations.
- Regressions become visible between releases rather than at the next manual
  read-through.
- **Traces on disk are not a reliable source** — `TRACE_DIR` is ephemeral on
  Render ([ADR 0006](0006-relational-database.md)). Aggregation must read
  `audit_events`, which survives redeploys. This is exactly the kind of
  constraint that only appears once you try to compute something from the data.

**Revisit when:** metric volume or query cost makes a purpose-built time-series
store cheaper than SQL over `audit_events`.
