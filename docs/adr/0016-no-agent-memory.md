# ADR 0016: No agent-level memory — state stays scoped to the request

**Status:** Accepted — `graph.py`, `orchestrator_agent/intake.py`, `database.py`

## Context

Agentic systems are generally expected to have a memory layer — something that
carries context across turns (short-term) and across sessions (long-term), the
way LangChain's `ConversationBufferMemory` or a per-user vector store would.
Neither the Flight Agent nor the Orchestrator has one, and it is worth
recording as a decision rather than letting it read as an oversight, the same
way [ADR 0007](0007-no-vector-database.md) does for retrieval.

It is tempting to assume `request_id` is quietly doing this job. It isn't:
it's `uuid4()`, generated fresh per request (`jobs.py`, `api.py`) unless a
caller supplies one (an A2A `task.id`, or an intake session id being carried
forward into the plan it produced). It is a **correlation key** — it ties one
plan's audit trace, `planning_jobs`, `agent_runs`, `options` and
`intake_messages` rows together — not a lookup key into anything that
persists knowledge about a traveller.

Two distinct questions hide inside "does it need memory":

1. **Short-term** — does a node need to remember something across multiple
   turns of the *same* interaction?
2. **Long-term** — should a new plan be informed by a traveller's *previous*
   trips or feedback?

## Options

1. **Give specialists/orchestrator their own memory object**, threaded through
   `TravelGraphState` or held by the node, covering both turns and sessions.
2. **Keep every node stateless per request.** Scope any genuinely
   conversational context to the one layer that is actually conversational
   (intake), as ordinary rows keyed by `request_id`. Do not build cross-trip
   recall.

## Decision

Option 2.

**Short-term:** the graph is a single-pass fan-out/fan-in DAG per request
([ADR 0001](0001-fan-out-fan-in-graph.md)) — each specialist and the
orchestrator run exactly once per plan. There is no second turn for a node to
remember, so a memory object on the node would have nothing to hold. The one
place multiple turns genuinely happen is the intake chat *before* a
`TravelRequest` exists at all, and that already has exactly the memory it
needs: `intake_messages`, one row per message, keyed by `request_id`, replayed
by `get_intake_conversation` into `extract_intent`
(`orchestrator_agent/intake.py`). That's a plain, auditable database log, not
a memory abstraction — which is the right amount of machinery for "remember
what was said earlier in this one chat."

**Long-term:** no plan is informed by a traveller's history today, and that's
consistent with the two decisions this project has already made. ADR 0002
("code decides, model explains") requires the Flight Agent's ranking to be a
pure, deterministic function of `(inventory, this request)` — that's what
`test_flight_golden.py` and `test_flight_bias_audit.py` pin byte-for-byte.
Folding in an LLM-recalled memory of past trips would make ranking a function
of history those tests cannot see or replay, which breaks the reproducibility
the whole design argument in `docs/flight_agent/design.md` rests on. ADR 0007
makes the adjacent point for retrieval: exact set membership beats semantic
recall wherever the answer is decidable, and "what did this traveller like
last time" is not the kind of fuzzy-relevance problem a memory store is good
at either — it's a handful of explicit fields.

`travel_requests` and `plan_feedback` are written on every plan and already
carry everything a future personalization feature would need; nothing reads
them back in. That is the actual gap, not a missing memory framework.

## Consequences

- Ranking and synthesis stay reproducible: same request in, same options out,
  independent of when it's asked or what the traveller asked for last month.
- No new memory-store dependency (no conversation-buffer library, no
  per-user vector index) for either agent.
- The intake chat's context-carrying need is met by a database table any
  reviewer can read directly — no separate memory subsystem to audit.
- The audit trace stays the single source of truth for "what happened in this
  plan." A memory object with its own state would be a second, harder-to-trace
  place for behaviour to hide.
- **Real, current gap:** nothing personalizes a plan using a traveller's past
  trips or ratings. "You usually prefer aisle seats" or "you rejected this
  airline last time" is not implemented.

**Revisit when:** a feature genuinely requires cross-trip personalization.
Design it as a **deterministic pre-request enrichment step** — read
`users`/`travel_requests`/`plan_feedback` for that `user_id` and fold the
result into the `TravelRequest` payload before the graph runs — not as memory
an agent reaches for mid-reasoning. That keeps it an explicit, auditable input
`domain.py` and the golden tests can see, rather than a second decision path
running behind the one they already pin.
