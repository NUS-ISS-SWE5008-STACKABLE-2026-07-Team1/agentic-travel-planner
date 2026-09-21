# ADR 0014: Threads, not asyncio

**Status:** Accepted

## Context

"Make the orchestrator asynchronous" is a natural request for a system where a
plan takes 30-90 seconds. It is worth separating three different things the
phrase can mean, because two of them are already done.

| Meaning | State |
|---|---|
| The client is not blocked while planning runs | **Already true** — `jobs.py` + `GET /status` polling |
| The four specialists run concurrently | **Already true** — LangGraph fan-out (`graph.py:70`) |
| The Python code uses `async`/`await` | Not done — and this ADR is about why |

## Options

1. **Threads.** `ThreadPoolExecutor` for jobs, LangGraph's threaded fan-out.
2. **asyncio.** Convert nodes to `async def`, use LangGraph's async execution.

## Decision

Option 1.

### The barrier is a data dependency

The orchestrator waits for all four specialists because it *synthesises* all
four. That is not a scheduling artifact — asyncio would produce exactly the same
wait. The critical path is `max(specialist latency) + orchestrator latency` in
both models.

### Threads already win the parallelism that matters

Every expensive operation is a network call to a model provider. CPython
releases the GIL during I/O, so four concurrent LLM calls genuinely overlap on
threads. asyncio's advantage is scale — tens of thousands of idle connections —
and this system runs 4 concurrent plans on 8 request threads
([ADR 0005](0005-single-worker-in-process-queue.md)).

### The cost is not local

`async` colours every caller. Nodes, `agents/base.py`, the guardrail classifier,
providers, `a2a_client`, and both database paths would all need async variants
or thread-pool bridges. `psycopg` sync and `sqlite3` are blocking, so the
database would need `run_in_executor` anyway — reintroducing the thread pool
underneath the async layer, for no gain.

## Consequences

- Debugging stays conventional: real stack traces, no event-loop reentrancy.
- The ceiling is thread count, which is fine at 4 concurrent plans and would not
  be at 400.
- `a2a_client.py:99` creating a `ThreadPoolExecutor(max_workers=1)` per remote
  call is thread-per-request. Acceptable now; the first thing to fix if remote
  transport becomes the default.

## The real improvement this request points at

Not asyncio — **streaming**. Today a traveller sees nothing for 30-90 seconds,
then everything. Each specialist's finding could be pushed to the UI as it
completes (SSE or WebSocket over the existing job), turning one long wait into
four short ones.

That is a **perceived-latency** change, and it is worth more to a user than any
concurrency-model change, because total latency is bounded by the model provider
either way. It needs no async rewrite: the results already arrive independently,
they are simply held until the barrier clears.

**Revisit when:** concurrent plans reach the hundreds, or streaming demands
push-based transport that the thread model serves badly.
