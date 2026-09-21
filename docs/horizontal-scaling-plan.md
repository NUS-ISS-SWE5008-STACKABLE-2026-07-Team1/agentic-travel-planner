# Horizontal scaling plan

Companion to [ADR 0017](adr/0017-fix-data-location-before-adding-replicas.md),
which holds the decisions. This document holds the work, the targets, and the
measurements they were set from.

---

## 1. The three numbers

Targets are only useful if a run can fail them, so each is stated with what was
actually measured and how much headroom that leaves.

### Concurrency: **2 concurrent plans**

Chosen as the smallest number that means anything: more than one. Two plans
proves the system is not serialising work, and it is the number the current
deployment must keep passing while the phases below are built.

**Measured 2026-09-19** (`deploy/load_test.py --plans 2`, real model, live
cluster): 2/2 completed, **0** status-poll `404`s.

Worth being clear about what that run does and does not show. It proves plans
run concurrently *inside one process*. It cannot yet prove the failure ADR 0005
describes, because there is only one replica for a poll to land on. **The same
command against 2+ replicas after Phase 1 is the test that proves the fix** —
that is why it counts `404`s specifically and separately from other errors.

### Latency: **p95 ≤ 300s per plan at 2 concurrent**

| Measured (2 concurrent, real model) | |
|---|---|
| Plan latency | 206.8s, 246.5s |
| Submit (`POST /travel-plans`) | 10.3s, 10.6s |
| Specialists, single plan | 42-82s each |

**Why 300s and not something tighter.** Nearly all of it is the model's time,
not ours: four specialists at 42-82s, then synthesis, then an output guardrail
measured at 30s. A target below ~250s would fail on provider variance alone and
teach the team to ignore it. 300s sits ~20% above the slowest observed run — a
real regression trips it, a slow afternoon at OpenAI does not.

Two sub-targets matter more to a person using it:

- **Submit p95 ≤ 15s.** The traveller stares at a blank screen for this.
  Measured 10.6s, and it is *inline work*: the L2 input guardrail runs before
  the `202` (8s budget, now genuinely 8s — it used to retry twice silently).
- **Status poll p95 ≤ 1s.** Cheap today; the number to watch when the status
  endpoint starts reading from the database instead of memory in Phase 1.

### Cost: **not measurable today — fix before using it to plan**

| Plan | Tokens counted | Cost of that portion at gpt-5 rates |
|---|---|---|
| `d9c7adba…` | 15,319 in / 14,198 out | ~USD 0.16 |
| `d6ef905b…` | 9,247 in / 11,750 out | ~USD 0.13 |

At gpt-5's published USD 1.25 / 1M input and USD 10 / 1M output. **Treat both
as floors, not costs.** Only `accessibility_agent` and `orchestrator_agent`
report usage into `audit_events`; flight, hotel and risk report none, and both
guardrail gates are unaccounted. `flight_agent_eval_runs.input_tokens`,
`output_tokens` and `estimated_cost_usd` are **0 / NULL on every row** — the
columns exist and nothing fills them.

**Planning figure until then: USD 0.35 per plan**, roughly double the measured
floor. Confirm against the OpenAI dashboard for the `travel-planner-gke`
project, which has run only these tests and is therefore an exact source.

**Why this number is the one that matters.** At 0.35 a plan, 1,000 plans a day
is USD 350/day, against SGD ~1.45/day for the pods. **Scaling this system is a
model-spend decision wearing an infrastructure costume**, and no capacity
decision can be made honestly while the columns read zero.

---

## 2. What blocks a second replica

| # | Blocker | Where | Binds at |
|---|---|---|---|
| 1 | Job state in module-level dicts | `travel_ai/jobs.py:27-32` | 2 web replicas |
| 2 | Tasks in `InMemoryTaskStore` | `travel_ai/a2a_standard.py:572` | 2 agents replicas |
| 3 | A connection per database call, no pool | `database.py` (25 sites); `save_audit_event` fires per trace event | ~2-3 replicas |
| 4 | Schema DDL on every pod start | `flaskapp/__init__.py` → `initialize()` | every scale-up |

3 and 4 are not in any ADR and were found preparing this plan. **3 is the
nearest ceiling**: one observed plan wrote 31 audit events, each opening and
closing its own connection, against a 60-connection Supabase limit.

---

## 3. Phase 1 — more than one `web` replica (8-10 days)

| Work | Detail |
|---|---|
| Job state into `planning_jobs` | The table, `create_planning_job` and `update_planning_job` already exist. Status reads the row, not the dict |
| Rebuild `PlanResponse` from the database | `save_plan` already writes it across `travel_plans` / `agent_findings` / `options`; a loader is missing. The admin dashboard queries the same shape |
| Cancellation via the database | Replace the `threading.Event` with an object exposing `.is_set()` that reads a `planning_jobs` flag. `build_travel_graph(cancel_event=…)` already accepts anything with that method |
| **Connection pooling** | `psycopg_pool`, one pool per process; batch or buffer audit-event writes so a plan does not open ~31 connections |
| Traces from `audit_events` | Serve `/api/v1/traces/<id>` from the table. Fixes the ephemeral-disk loss *and* the half-a-trace symptom in one change |
| **Schema changes out of startup** | A `Job` or init container, run once per deploy |
| Fill the usage columns | So Phase 3 capacity decisions can be costed |

**Done when:** `--plans 2` passes against `web` scaled to 2, with **0** `404`s,
and a pod deleted mid-plan does not lose the plan.

---

## 4. Phase 2 — more than one `agents` replica (4-6 days)

| Work | Detail |
|---|---|
| Shared `TaskStore` | ~80 lines over `connect()`, plus an `a2a_tasks` table in **both** dialects — `tests/test_schema_parity.py` enforces that |
| Authentication on A2A | A bearer token, declared in the Agent Card's `securitySchemes`. Today a NetworkPolicy substitutes for it |

**Done when:** `agents` runs 2 replicas and a plan still completes with four
`a2a_call_completed` events, with the Service round-robining between pods.

---

## 5. Phase 3 — planning as its own tier (8-10 days)

This is the phase that makes the system scalable rather than merely replicated.

| Work | Detail |
|---|---|
| Split planning out of `web` | `web` accepts and answers; a `planner` Deployment does the work |
| Claim with `SELECT … FOR UPDATE SKIP LOCKED` | The job table becomes the queue |
| **Leases and heartbeats** | Without them a planner dying mid-plan leaves a job `processing` for ever. Requeue on expiry; make `save_plan` idempotent per `request_id` |
| Autoscale on queue depth | Not CPU: these pods wait on the model. Custom metric via Managed Prometheus |
| Graceful drain | Stop claiming, finish the current plan, exit. Grace period must exceed the longest plan (~250s measured) |

**Done when:** queue depth drives replica count, and killing a planner mid-plan
results in the plan finishing on another one.

---

## 6. Phase 4 — survive being popular (~8 days)

Rate limiting (**none exists anywhere today**, at ingress or in the app); the
guardrail verdict cache and inventory cache moved to Redis so replicas share
them; per-agent audit chains; traces to object storage; spend guards that stop
accepting work rather than discovering the bill later; and dashboards for queue
depth, p95 per agent, and tokens per plan.

---

## 7. Deliberately not doing

| Not doing | Why |
|---|---|
| Splitting into five agent services | They run together, once per plan. Hops and complexity, no independent scaling. The first real argument for it is *organisational* — a second team owning an agent |
| Adding a broker now | The database is already the queue. Revisit when polling it is measurably the bottleneck |
| A service mesh | [ADR 0012](adr/0012-no-service-mesh.md): five components, one team, one language |
| Autoscaling before Phases 1-2 | Kubernetes would schedule replicas that answer `404` for each other's work |
| Tuning pod sizes | At 2 concurrent plans they used under 0.18 of a 0.25-core request |

---

## 8. How each phase is proved

`deploy/load_test.py` is the instrument, run against the **stub provider** for
free, deterministic repetition and against the real model for the numbers in
§1:

```
docker compose -f deploy/docker-compose.yml up -d --build   # stub model
python deploy/load_test.py --base-url http://127.0.0.1:5000 --plans 2
```

It asserts every plan completes and counts status-poll `404`s separately,
because that specific failure is the whole point of the exercise. What it needs
next, as the phases land: kill a pod mid-run, assert no plan is lost.
