# ADR 0004: Transport and mode are bound at composition time

**Status:** Accepted — `graph.py:19-40`, `flight_agent/agent.py:290`

## Context

Three separate settings change how the system behaves at runtime:

- `FLIGHT_AGENT_TRANSPORT` — call the flight agent in-process or over A2A HTTP
- `FLIGHT_AGENT_MODE` — single-shot reasoning, tool loop, or `auto`
- guardrail model selection per gate

Each could be resolved per request, inside the node, or once when the graph is
built.

## Options

1. **Resolve inside the node, per request.** Maximum flexibility; a request
   could opt into a different path.
2. **Resolve once at composition time**, when the graph is built.

## Decision

Option 2, consistently, for all three.

Two reasons, and the first is not obvious. The A2A *server* builds its
specialist nodes from the same registry the in-process graph uses. A branch
inside `create_node` would therefore make the served flight agent **call
itself over HTTP** — an infinite loop across a network boundary.

The second reason is behavioural: a traveller's plan must not change strategy
underneath them mid-run. Resolving once means the mode that started the plan is
the mode that finishes it.

## Consequences

- **This is the seam that makes decomposition real rather than hypothetical.**
  The flight agent already runs both in-process and as a remote HTTP service,
  chosen by one environment variable, with the same tests passing either way.
  Every claim in [ADR 0013](0013-containerise-defer-kubernetes.md) about
  splitting into services rests on a boundary that is exercised today, not a
  refactor that is merely imagined.
- Changing transport or mode requires a restart. Correct for a deployment
  setting; it would be wrong for a per-tenant one.
- Discovery is already externalised to configuration — `FLIGHT_AGENT_A2A_URL`
  and `A2A_BASE_URL`. Moving to Kubernetes DNS
  (`http://flight-agent.default.svc.cluster.local`) is therefore a **value
  change, not a code change**. No service-discovery client library is needed;
  environment-variable-plus-DNS is the discovery mechanism.
- Composition-time binding is now an established pattern in this codebase.
  A new setting that alters agent behaviour should follow it by default.
