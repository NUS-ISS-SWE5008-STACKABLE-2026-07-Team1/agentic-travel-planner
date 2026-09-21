# ADR 0001: Parallel specialists joined at a synthesis barrier

**Status:** Accepted — retrospective record of the design in `graph.py`

## Context

A travel plan needs four kinds of expertise — flights, hotels and ground
transport, accessibility, and risk advisory — plus a synthesis step that turns
four partial answers into one itinerary a traveller can read.

The four specialists have no data dependency on each other. A hotel search does
not need the chosen flight to run; it needs the *request*. The synthesis step,
by contrast, depends on all four by definition.

## Options

1. **Sequential chain** — flight, then hotel, then accessibility, then risk.
   Simple, and lets each agent see its predecessor's output.
2. **Parallel fan-out, fan-in at the orchestrator.** Four specialists run
   concurrently from the shared typed state; the orchestrator waits for all.
3. **Fully autonomous agent swarm** — the orchestrator decides at runtime which
   specialists to invoke and may re-invoke them.

## Decision

Option 2. `graph.py` adds `START -> specialist` for each of the four, then a
single list-valued edge `add_edge(list(SPECIALISTS), "orchestrator_agent")`,
which LangGraph treats as a barrier.

Option 1 was rejected on latency: four LLM round-trips in series, each 3-10s
(see `docs/flight_agent/mode-eval.md`), is a four-fold multiplication of the
slowest part of the system for a dependency that does not exist.

Option 3 was rejected because a variable agent set makes the output
non-reproducible, and reproducibility is what the golden-scenario suite and the
bias audit rest on. It also has no upper bound on cost per plan.

## Consequences

- Wall-clock for the specialist phase is the **slowest** agent, not the sum.
- The specialists cannot negotiate with each other. Cross-agent constraints
  (a flight arriving after hotel check-in closes) surface only at synthesis.
  `FlightConstraints` and `negotiation_history` exist for a renegotiation loop,
  but nothing issues them mid-run — see the "not yet true" note in
  `docs/individual_reports/flight_agent.md`.
- The barrier is a **data dependency, not a scheduling choice**. No concurrency
  model removes it; see [ADR 0014](0014-threads-not-asyncio.md).
- Adding a fifth specialist is a registry entry in `agents/__init__.py`, not a
  graph rewrite.
