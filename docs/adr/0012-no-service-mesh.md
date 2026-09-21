# ADR 0012: No service mesh

**Status:** Rejected

## Context

If the agents are decomposed into services
([ADR 0013](0013-containerise-defer-kubernetes.md)), the standard next question
is whether a service mesh (Istio, Linkerd) should handle inter-service traffic:
mTLS, retries, timeouts, circuit breaking, traffic shifting, and per-hop
telemetry.

## Decision

No mesh, at this scale or the next one.

### The cardinality argument

A mesh solves problems that appear when **many** services, written by **many
teams**, in **many languages**, need uniform network policy that no single team
can implement consistently. Its value curve starts around dozens of services.

This system is five agents, one language, one team, one trust boundary. Every
capability a mesh would add is either already present or unnecessary:

| Mesh capability | Position here |
|---|---|
| mTLS between services | The agents share one trust boundary. Render terminates TLS at the edge |
| Retries / timeouts | `build_llm` sets `max_retries=2`; `LoopBudget` caps wall clock; A2A calls take an explicit timeout |
| Circuit breaking | Superseded by [ADR 0011](0011-degrade-visibly.md) — a failing dependency degrades to a usable answer rather than tripping a breaker |
| Distributed tracing | `AuditTracer` hash-chains events with `correlation_id` across every hop, in-process or HTTP. **Richer than a mesh could provide** — it records agent decisions, not just spans |
| Traffic shifting | One version deployed at a time |

### The cost

A sidecar per pod (memory, and latency on every hop), a control plane to run and
upgrade, and a new failure domain — mesh misconfiguration presents as
application failure, which is expensive to debug in a five-service system that
had no network problem to begin with.

## Consequences

- Cross-cutting concerns stay in application code, where this team can read them.
- **The mesh's observability case is already answered better.** A mesh sees
  requests; `AuditTracer` sees why an agent chose a flight, and
  `verify_hash_chain()` proves the record was not altered afterwards.
- If growth demands infrastructure, the correct order is: **ingress/gateway
  first** (TLS termination, routing, and the rate limiting that is currently
  missing entirely), *then* reconsider a mesh. A gateway solves a problem that
  exists today; a mesh solves one that does not.

**Revisit when:** services exceed roughly a dozen, a second language enters the
stack, or agents must run in separate trust boundaries requiring mTLS between
them.
