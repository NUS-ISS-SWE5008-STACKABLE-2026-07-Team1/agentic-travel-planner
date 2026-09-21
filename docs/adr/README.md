# Architecture Decision Records

One record per significant decision: what forced it, what we considered, what we
chose, and what it now costs us. Records are immutable once accepted — a change
of mind is a new ADR that supersedes an old one, never an edit.

Most of these are **retrospective**: the decision was already made and lives in a
code comment or a docstring. Writing it here moves the reasoning somewhere a
reviewer will actually find it. Where a record is a proposal rather than a
description of the running system, its status says so.

| # | Decision | Status |
|---|---|---|
| [0001](0001-fan-out-fan-in-graph.md) | Parallel specialists joined at a synthesis barrier | Accepted |
| [0002](0002-code-decides-model-explains.md) | Code decides, the model explains | Accepted |
| [0003](0003-a2a-envelope.md) | A versioned envelope is the only cross-agent interface | Accepted |
| [0004](0004-composition-time-binding.md) | Transport and mode bound at composition time | Accepted |
| [0005](0005-single-worker-in-process-queue.md) | Single worker, in-process job queue | Accepted |
| [0006](0006-relational-database.md) | Relational database; Postgres via session pooler | Accepted |
| [0007](0007-no-vector-database.md) | No vector database | Rejected |
| [0008](0008-reasoning-strategy-routing.md) | Route reasoning strategy, not models | Accepted |
| [0009](0009-tiered-guardrail-models.md) | Task-tiered guardrail models, statically bound | Accepted |
| [0010](0010-layered-caching.md) | Three caches at three scopes; no shared cache | Accepted |
| [0011](0011-degrade-visibly.md) | Every failure degrades visibly | Accepted |
| [0012](0012-no-service-mesh.md) | No service mesh | Rejected |
| [0013](0013-containerise-defer-kubernetes.md) | Containerise now, defer Kubernetes | Proposed |
| [0014](0014-threads-not-asyncio.md) | Threads, not asyncio | Accepted |
| [0015](0015-metrics-aggregation.md) | Aggregate trace events into metrics | Proposed |
| [0016](0016-no-agent-memory.md) | No agent-level memory — state scoped to the request | Accepted |
| [0017](0017-fix-data-location-before-adding-replicas.md) | Fix data location before adding replicas | Proposed |

## Format

Michael Nygard's template. Context / Options / Decision / Consequences, plus a
**Revisit when** line on anything deferred, so a rejection carries its own
expiry condition instead of hardening into dogma.
