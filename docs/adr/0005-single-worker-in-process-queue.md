# ADR 0005: Single worker, in-process job queue

**Status:** Accepted — 2026-08-20. **This is the system's scaling ceiling.**

## Context

Planning takes 30-90 seconds. An HTTP request cannot block that long, so
planning is asynchronous: `POST` returns a `request_id` immediately and the
client polls `GET /api/v1/travel-plans/<id>/status`.

`jobs.py` runs the work on a `ThreadPoolExecutor(max_workers=4)` and holds job
state in **module-level dicts** — `_jobs`, `_cancel_events`, `_futures`.

## Options

1. **Multiple Gunicorn workers.** Standard horizontal scaling within one host.
2. **One worker, many threads.** `--workers 1 --threads 8`.
3. **External broker + separate worker process** (Redis/RabbitMQ + Celery/RQ).

## Decision

Option 2, deliberately, and `render.yaml` says why at the point of configuration.

A module-level dict is per-process. With two workers, `GET .../status` would be
answered by whichever worker the load balancer picked — which may not be the one
running the job — returning **404 while the plan is running fine in its
sibling**. Threads share the dict, so they share the state.

Option 3 is the correct end state and was rejected only on timing: it costs a
broker, a second deployable, and a serialization contract, to solve a load
problem this project does not have.

## Consequences

- **The application cannot scale beyond one node.** This is the binding
  constraint on the whole system, and it is one data-location problem, not an
  architectural flaw in the agents.
- Concurrency is real but bounded: 4 planning threads, 8 request threads.
  Since the work is I/O-bound on LLM calls, threads are the right primitive
  (see [ADR 0014](0014-threads-not-asyncio.md)).
- `a2a_client.py:99` opens a `ThreadPoolExecutor(max_workers=1)` per remote
  call — thread-per-request. Fine at this scale, a real cost at higher load.
- No rate limiting exists anywhere. Resource exhaustion is listed as **Not
  built** in `docs/individual_reports/flight_agent.md`. At one worker, a modest
  request volume is a denial of service.

## The unlock, in order

1. Move job state from the module dicts into the **`planning_jobs` table** that
   already exists. This alone permits `--workers > 1`.
2. Add a broker only when more than one *node* is needed. The DB-backed job
   table is a queue; a broker earns its place when polling it becomes the
   bottleneck, not before.

**Revisit when:** concurrent plans regularly exceed 4, or p95 queue wait exceeds
one planning duration.
