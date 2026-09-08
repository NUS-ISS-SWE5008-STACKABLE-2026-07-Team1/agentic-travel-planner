# Flight Agent over Google A2A — findings and plan

Rewritten 2026-09-06. **The first version of this file (2026-09-05) was wrong on
its central claim** — it said the A2A plumbing was "done and correctly tested."
It is neither. Corrections below, then the agreed plan.

---

## What the first version got wrong

1. ~~**`a2a-sdk` is not installed.**~~ **This claim was itself wrong** and is
   retracted (2026-09-06). `a2a-sdk 1.1.2` and `starlette 1.6.0` are installed,
   and `tests/test_a2a_standard.py` + `tests/test_combined_asgi.py` pass. The
   claim came from a subagent's report of a `ModuleNotFoundError` that I relayed
   without running the tests myself. Verified before writing anything else this
   time — the lesson being that "I read the test file" is not "I saw it pass."

2. **There is a blocking bug, and it is not flight-specific.** *(Confirmed
   empirically, not just read: `save_agent_run` against a fresh initialized DB
   with no job row raises `IntegrityError: FOREIGN KEY constraint failed`.)*
   `agent_runs.request_id` has `REFERENCES planning_jobs(request_id)`
   (`database.py:153-163`) and `PRAGMA foreign_keys = ON` is set on every
   connection (`database.py:503`). The A2A path passes `task.id` as the request
   id (`a2a_standard.py:158`) and **never creates a `planning_jobs` row**, so
   `save_agent_run` raises IntegrityError, which the blanket
   `except Exception` (`:180-185`) converts into an opaque failed task.
   It hits **all four specialists** — the same calls live in
   `agents/base.py:47-48, 59-61, 89-91, 97-99`, not just in the flight agent.
   Tests missed it because `test_flight_node.py:77` uses `database_path=None`,
   which short-circuits every `if tracer.database_path:` guard.

3. **The conformance surface is larger than the three items listed.**
   The card advertises `streaming=True` with no streaming (`:92`); `cancel`
   marks state but cannot stop the thread (`:187-191`); errors collapse to one
   opaque string instead of the `-32001..-32006` taxonomy; each agent gets its
   own unbounded `InMemoryTaskStore` (`:251`); display names come from string
   surgery that only works for the flight agent (`:81`).

Two things the first version got right and still hold: no guardrails on the A2A
specialist path, and no auth on `/a2a/*`.

---

## Decisions taken

| Question | Decision |
|---|---|
| Orchestrator → flight transport | **Switchable.** In-process stays the default; one setting routes through real A2A. Mirrors the `FLIGHT_AGENT_MODE` one-variable-revert precedent. |
| Definition of done | **Working + TCK-verified.** Fix the bug, real-node tests, guardrail parity, then run Google's official TCK and fix what it flags. |
| Streaming | **`streaming: false`.** Nothing consumes it — the loading bar uses trace polling (`app.js:417,446`) and the orchestrator waits at a barrier for all four specialists (`graph.py:48`). Dropping the claim also keeps the TCK from testing it. |
| Audit trail | **First-class.** Create a `planning_jobs` row for A2A requests and carry the parent request id across the hop so the hash chain stays unified. |

---

## Corrections to the decisions, found while designing

- **`security_schemes` is *not* required for a conformant v1.0 card.** The
  protobuf `AgentCard` makes it optional, and the field is
  `security_requirements`, not `security` (that's the 0.x JSON name). A card
  with no scheme declares an open agent, which is what we have. **Declaring a
  scheme we don't enforce would be worse than declaring none** — so either
  declare + enforce, or declare nothing.
- **"One unbroken hash chain" only holds in a single process.**
  `AuditTracer._lock` is a module-level `threading.Lock` (`tracing.py:12`).
  Two processes appending to the same `{request_id}.jsonl` will produce a chain
  `verify_hash_chain` rejects. **Therefore: deploy `combined.py` (one process).**
  If the A2A sidecar is ever split out, switch to a child trace
  (`f"{parent}-flight"`) with an `a2a_delegated` event linking them.
- **`create_planning_job` upserts to `status='queued'`** (`database.py:726-731`).
  Calling it on the propagation path would reset a live parent job and corrupt
  `/admin`. Needs an `ensure_planning_job` variant with `ON CONFLICT DO NOTHING`.
- **`/api/v1/traces` will still 404 for anonymous A2A callers, and should.**
  `owns_request` returns False when `user_id is None` (`database.py:772-773`),
  deliberately. Propagation fixes traces for the *orchestrator→flight* hop
  (parent job owned by the signed-in user), not for external callers.
- **`render.yaml` must change or the switch is a footgun.** `startCommand` is
  `gunicorn "flaskapp:create_app()"` — WSGI only. Turning the A2A transport on
  in production without moving to `uvicorn asgi:application` points the
  orchestrator at a URL nothing serves.

---

## Plan, in dependency order

### ✅ Phase 1 — done 2026-09-06

**0. Baseline.** ✅ `a2a-sdk 1.1.2` present; all A2A tests pass. Still open:
the pin is an open range (`>=1.1,<2`) with no lockfile on a young 1.x line —
worth tightening to `>=1.1.2,<1.2`, not done yet.

**1. `ensure_planning_job`** ✅ in `database.py`, next to `create_planning_job`:
same INSERT with `ON CONFLICT DO NOTHING`, returns whether it inserted.
`create_planning_job` left alone — `jobs.submit_plan` relies on its
reset-to-queued semantics for resubmits, and a test now pins that contrast.

**1b. The blocking bug is fixed.** ✅ `SpecialistAgentExecutor` takes an
optional `database_path`, creates the job row before the node runs, and marks
the job terminal afterwards so `/admin` doesn't show it stuck at queued.
`build_a2a_application` and `scripts/a2a_server.py` pass it through.
`tests/test_a2a_executor_persistence.py` is the regression — it runs the
**real** flight node against a **real** database, the two properties whose
absence let this ship. Full suite: 752 passed, 13 skipped.

### ✅ Phase 2 — done 2026-09-06

**Corrections found while implementing — the plan had these wrong, and each
would have broken the Phase 3 client:**

- **v1.0 JSON-RPC methods are PascalCase**: `SendMessage`, `GetTask`,
  `CancelTask`, `ListTasks`. `message/send` is **v0.3**, served via a compat
  adapter.
- **`A2A-Version: 1.0` is a required HTTP header.** Without it the server
  assumes v0.3 and rejects with `-32009 VersionNotSupported`. **The Phase 3
  client must send this header.**
- **`security_schemes` is optional** and the field is `security_requirements`.
  Card declares none, honestly, since we enforce none.

**Done:**
- `a2a-sdk` pinned to `==1.1.2`.
- Screening moved **before** task creation. Caller mistakes now raise A2A
  errors (`-32005` wrong content type, `-32602` invalid params); only failures
  during the work produce a failed task. `A2AError` is re-raised rather than
  swallowed by the blanket handler. Exception class names no longer leak to
  callers.
- Guardrail parity: `_screen_request` runs `validate_request` then
  `screen_request_l2`, same order as `api.py`, reusing those functions. The L2
  block message stays vague, asserted by test.
- `ExecutorContext` replaces loose constructor args; built via
  `from_flask_config`.
- Card: `streaming=False`, explicit `display_name` per agent, implementation
  version separated from protocol version, `AGENT_CARD_WELL_KNOWN_PATH` /
  `PROTOCOL_VERSION_1_0` constants, root card served with `A2A_ROOT_AGENT`
  selecting which agent answers there. One shared task store.
- `tests/test_a2a_error_taxonomy.py` added. Suite: **765 passed, 13 skipped**.
- Verified over real HTTP end to end, not only via direct executor calls.

### ✅ Phase 3 — done 2026-09-06

The orchestrator can now call the flight agent over real Agent2Agent.
`FLIGHT_AGENT_TRANSPORT=a2a` (default `inprocess`), resolved in `graph.py` at
build time. **It must not be resolved inside `flight_node`** — the A2A server
builds its nodes from the same registry, so a branch there would make the
served agent call itself over HTTP.

**Protocol facts found by probing, not in the plan:**
- `SendMessage` returns a task handle, not a result. Poll `GetTask` to a
  terminal state.
- `RequestContext.metadata` exposes **`SendMessageRequest.metadata`**, not the
  Message's. Metadata on the message arrives unreadable.
- The SDK client sets `A2A-Version` itself; hand-rolled HTTP would be rejected
  as v0.3.

**New:** `flaskapp/travel_ai/a2a_client.py`, `tests/test_a2a_client_node.py`
(real server + real SDK client over `httpx.ASGITransport` — CI-safe).
Settings documented in `.env.example`. Suite: **774 passed, 13 skipped**.

**Note the coupling:** request-id propagation is what keeps one audit chain
across the hop, but a request id is a write key into an existing trace, so it
is honoured only under `A2A_TRUST_CALLER_REQUEST_ID=true`. Enabling that on an
endpoint that is open to strangers is precisely the hazard the gate exists to
prevent — so auth (below) and that flag must be decided together.

### ✅ Phase 4 — done 2026-09-06. TCK run; results in `docs/a2a/`

**MUST compatibility 23.8% → 43.5%** (overall 19.7% → 39.2%).
Full write-up and the raw reports: [`docs/a2a/tck-results.md`](../a2a/tck-results.md).
Reproduce with `scripts/run_tck_sut.sh` (stub provider — nothing billed).

**The finding that justifies the whole phase:** one missing trailing slash
caused 41 of 53 failures. The TCK posts to `/a2a/flight_agent/`; we routed only
`/a2a/flight_agent`, so requests fell into the Flask catch-all and returned
404 HTML. Every generic A2A client would have hit this. No unit test would have
produced that URL.

**Blocked on a product decision:** 7 of the 8 remaining MUST failures are the
TCK driving lifecycle tests with plain text against an agent that requires a
structured `TravelRequest`. Rejecting with `-32005` is spec-legal and declared
on the card. Accepting text and replying `INPUT_REQUIRED` would raise the score
and reuse the orchestrator's existing `compute_gaps` / `clarification_question`
machinery — but it widens the agent's contract, so it needs deciding, not
slipping in. Both options in the results doc.

### Remaining

**2. `ExecutorContext`** — a frozen dataclass carrying config, `database_path`,
`trace_dir`, guardrail settings, `max_input_chars`, `trusted_metadata`. Both
executors take it; `scripts/a2a_server.py` builds it from `flask_app.config`.
Also restructure `create_application` so `node_factories` and
`orchestrator_runner` resolve **independently** — today the orchestrator default
is unreachable whenever factories are injected, so no test that injects
factories can exercise guardrail parity.

**3. Rewrite `SpecialistAgentExecutor.execute` — validate *before* the task exists.**
The most consequential structural change. Today the task is enqueued and
`start_work()` fires before any validation, which forces every rejection to
become a failed task. Invert it:

- *Phase 1, no task yet* — raise A2A errors: `InvalidRequestError` (no message),
  `ContentTypeNotSupportedError` (text/file parts only), `InvalidParamsError`
  (wrong part count, `SafetyError`, `GuardrailBlocked`, `ValidationError`).
  Call sequence mirrors `api.py:76-84` exactly:
  `validate_request(payload, max_chars)` → `screen_request_l2(request, LlmGuardrail.from_settings(...))`.
- *Phase 2, task created* — `InvalidAgentResponseError` if the node returns ≠1
  finding; `TASK_STATE_FAILED` if it raises.

Keep the block message discipline from `api.py:104-109`: log
`verdict.as_audit_details()`, record `guardrail_llm_verdict` on the tracer, and
return only the vague `_BLOCKED_MESSAGE`. The category must never reach the
client (`safeguards.py:55-59` explains why). Also drop `type(exc).__name__`
from the client-visible failure — that's internal information disclosure.

**4. Request-id propagation + `planning_jobs`.** Channel is
`message.metadata` (read via `RequestContext.metadata`), namespaced
`travelplanner/parent_request_id`. **Not** `contextId` — the SDK and TCK own its
lifecycle. Add a trust gate: only honour an inherited id when the caller
authenticated or the endpoint is loopback-bound, otherwise any anonymous caller
can write into another traveller's trace. Then `ensure_planning_job(...)`
before anything touches `agent_runs`, and guard status updates on
`not inherited` so the executor doesn't stomp a job `jobs.py` already owns.
One `AuditTracer` per execute, keyed on the resolved id.
**This is the blocking-bug fix, and it fixes all four specialists.**

**5. Agent Card corrections.** `streaming=False`; explicit `display_name` per
entry in `AGENT_SKILLS` (kills the `.replace(" planning", " Agent")` surgery
that mislabels three of five agents); `version` from a real
`flaskapp.__version__`; use `AGENT_CARD_WELL_KNOWN_PATH` / `PROTOCOL_VERSION_1_0`
constants. **Serve one card at the origin root** (the TCK fetches
`{host}/.well-known/agent-card.json` and cannot start without it) while keeping
per-agent cards at their sub-paths. Add `A2A_ROOT_AGENT` (default
`orchestrator_agent`) so the root can be pointed at `flight_agent` — that is how
the flight agent specifically gets TCK-certified.

**6. The A2A client node** — new `flaskapp/travel_ai/a2a_client.py`.
`FLIGHT_AGENT_TRANSPORT` (`inprocess` | `a2a`) resolved **at graph-build time,
not inside `flight_node`** — a branch inside `create_node` would make the A2A
server's own flight node call itself over HTTP. `graph.py` picks the factory.
Async-from-sync via `asyncio.run` on a thread with no running loop (single-digit
ms against 5.4–8.5 s of agent latency); build the `httpx.AsyncClient` inside the
coroutine. Memoize card resolution per node closure. Reconstruct the outgoing
`A2AMessage` client-side with `response_message` so `merge_messages` and
`save_plan` stay transport-agnostic.
**On failure: fail, don't fall back** — a silent fallback makes it impossible to
tell whether the A2A path actually ran, which defeats TCK-verifying it.

**7. Task store and cancel.** Hoist one shared `InMemoryTaskStore` instead of
five. Wire cancel to a `threading.Event` checked before/after the node, reusing
the in-process `PlanningCancelled` pattern (`graph.py:29-35`); return
`TaskNotFoundError` / `TaskNotCancelableError` instead of bare `ValueError`.

**8. Tests.** The defining property of the FK-bug test is **a real
`database_path`** — say so in a module docstring so nobody "simplifies" it back
to `None`. Also: guardrail parity (assert BLOCK creates *no task* and the
category never reaches the client), error taxonomy, a transport-parity test
(assert structural equality, **not** `summary` — the prose is non-deterministic
either way), and a JSON round-trip test pinning protobuf's int→float coercion.
Drive the client half over `httpx.ASGITransport` — no ports, no network, no
credentials, so it runs in CI.

**9. TCK.** Clone `a2aproject/a2a-tck` out-of-tree, pinned to a commit SHA.
Run the SUT via `uvicorn asgi:application` with `A2A_ROOT_AGENT=flight_agent`
and the stub provider. `./run_tck.py --sut-host … --transport jsonrpc`.
Expect to fail first on: card not at origin root (blocks everything else),
then the streaming claim, then cancel/get error codes, then malformed-request
codes. Add as a **separate non-blocking workflow**, modelled on `ci-dast.yml`,
not in `ci-fast.yml` (which is deliberately kept at ~2 minutes). Gate on
"MUST-level failures == 0" parsed from `compatibility.json`.

**10. Deployment and docs.** `render.yaml` → `uvicorn asgi:application`;
`.env.example` entries for the new settings; `CLAUDE.md` next to the
`FLIGHT_AGENT_MODE` section; update `mode-eval.md` with the *measured* A2A hop
latency from the `a2a_call_completed` trace events rather than the estimate.

---

## Where the milestone boundary sits

Steps 0–4 are "it actually works and is safe." Step 5 must land before step 9
or the TCK cannot start. Step 6 is the part that makes the flight agent
genuinely talk to the orchestrator over Google's protocol.

## Related

- [`docs/flight_agent/design.md`](../flight_agent/design.md) — the grounding argument behind guardrail parity
- [`frontendPlusOrchestratorExplained.html`](../../frontendPlusOrchestratorExplained.html) §16 — how `a2a_standard.py` bridges the synchronous nodes
