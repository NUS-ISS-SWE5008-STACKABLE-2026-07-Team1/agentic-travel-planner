# ADR 0017: Fix data location before adding replicas

**Status:** Proposed — nothing in this ADR is built

**Supersedes nothing. Extends [ADR 0005](0005-single-worker-in-process-queue.md)
(the scaling ceiling) and [ADR 0013](0013-containerise-defer-kubernetes.md)
(containerise, defer Kubernetes), both of which named the prerequisite and
neither of which was specific about the order of work.**

## Context

The application now runs on GKE as two roles — `web` (site, API, job manager,
orchestrator) and `agents` (all four specialists over official A2A). Both are
pinned to **one replica**, and the deployment has been exercised: real plans
complete, the specialists genuinely answer over the network.

Running it produced measurements the ADRs did not have.

**Measured on 2026-09-19, two concurrent plans, real model, against the live
cluster** (`deploy/load_test.py`, request ids `d9c7adba…`, `d6ef905b…`):

| Metric | Result |
|---|---|
| Plans completed | 2 / 2 |
| `404` from status polling | **0** |
| Plan latency | 206.8s and 246.5s |
| Submit (`POST /api/v1/travel-plans`) | 10.3s and 10.6s |
| Tokens per plan (partial, see below) | 21.0k and 29.5k |

So concurrency *within one process* already works: two plans run at once, and
every status poll finds its job. What does not exist is the ability to run a
second copy of either role.

Four things prevent that. Two are recorded; two were found while preparing this
ADR and are not recorded anywhere:

1. **Job state lives in module-level dicts** (`jobs.py`) — ADR 0005.
2. **A2A tasks live in an `InMemoryTaskStore`** (`a2a_standard.py:572`). The
   client sends `SendMessage` then polls `GetTask`; behind a Service with two
   replicas the poll can reach a pod that never saw the task.
3. **Every database call opens its own connection.** `connect()` is called at
   25 sites with no pool, and `save_audit_event` opens one **per trace event** —
   31 events in a single observed plan. Supabase allows 60 connections. This
   binds at roughly 2-3 replicas, *before* either memory problem becomes
   visible, which makes it the nearest ceiling and the least known.
4. **Schema changes run on every pod start.** `create_app` → `init_app` →
   `initialize()` executes DDL, so every pod a scale-up creates runs schema
   changes against the live database, concurrently.

A fifth is not a blocker but is measured and wrong: **token and cost columns in
`flight_agent_eval_runs` are always 0 / NULL**, and only 2 of the 5 agents
report usage into `audit_events` at all. Cost per plan therefore cannot be
derived from our own data — the measured 21k-29.5k tokens is a floor, not a
figure.

## Decision

**Replicas come last.** The work is ordered so that each phase ends with
something demonstrable, and no phase adds capacity the previous one cannot
support.

| Phase | Ends with | Effort |
|---|---|---|
| 1 | `web` can run more than one replica | 8-10 d |
| 2 | `agents` can run more than one replica | 4-6 d |
| 3 | Planning is its own tier, autoscaled on queue depth | 8-10 d |
| 4 | It survives being popular: rate limits, shared caches, durable traces | ~8 d |

The detail is in [`docs/horizontal-scaling-plan.md`](../horizontal-scaling-plan.md).

Four choices inside that plan are decisions rather than tasks:

**The database stays the queue.** `planning_jobs` already holds every submitted
plan; claiming work with `SELECT … FOR UPDATE SKIP LOCKED` turns it into one
without a new deployable. A broker (Redis, RabbitMQ) costs a component, a
serialisation contract and an operational surface to solve a load problem the
measurements do not show. Revisit when polling the table is measurably the
bottleneck — not before.

**The A2A task store is built, not adopted.** The SDK ships `DatabaseTaskStore`,
but it requires SQLAlchemy and an async driver, which would put a second way of
reaching the same database alongside the existing `psycopg` layer, and a second
migration story. Implementing `TaskStore` over `connect()` is roughly 80 lines
and one table in both dialects.

**The audit hash chain becomes per-agent.** `tracing.py` guards the chain with a
module-level `threading.Lock` and seeds it from the last line of a per-pod file.
That cannot hold across processes. One chain per agent per request keeps every
event tamper-evident and loses only the global ordering between agents, which
`timestamp` already gives approximately. The alternative — one chain with a
locked head row — preserves global order at the cost of contention on the
hottest write path in the system. **This matters beyond tidiness:**
[ADR 0012](0012-no-service-mesh.md) rejects a service mesh partly because
`AuditTracer` is richer than mesh telemetry, so the chain has to keep working
once there is more than one process.

**Cancellation goes through the database.** `cancel_event` is a
`threading.Event` that the graph only ever asks `.is_set()`. A small object with
the same method, polling a `planning_jobs` flag, substitutes directly — no new
infrastructure, and it works whichever pod is running the plan.

## Consequences

- **Phases 1-2 (~15 days) deliver most of the practical benefit**: two or more
  replicas of each role, so a pod eviction or a node upgrade stops being an
  outage. Autopilot evicted both pods four times in one day before a
  PodDisruptionBudget stopped it; with replicas, that class of event is routine
  rather than damaging.
- **Phase 3 is what makes it *scalable* rather than merely replicated**, because
  it lets the expensive tier scale on a signal that reflects demand. It also
  introduces leases and heartbeats, without which a planner dying mid-plan
  leaves a job `processing` forever — invisible today with one pod.
- **Compute is not the binding constraint.** At 2 concurrent plans the pods used
  under 0.18 of a 0.25-core request. The real ceilings are provider rate limits,
  the 60 database connections, and money. Phase 1's pooling work therefore buys
  more headroom than any pod sizing.
- **Cost per plan cannot currently be measured**, so no capacity decision can be
  costed. Fixing the usage columns is small and belongs in Phase 1.
- Anything in Phase 3-4 is wasted if Phases 1-2 are skipped: Kubernetes would
  faithfully schedule replicas that answer `404` for each other's work.

**Revisit when:** any of the three targets in the plan document is missed in a
measured run, or a second team needs to deploy an agent independently — which
is the first argument for per-agent services that this system does not yet have.
