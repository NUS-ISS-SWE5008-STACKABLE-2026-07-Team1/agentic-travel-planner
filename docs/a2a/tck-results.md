# A2A conformance — TCK results

Run 2026-09-06 against the **flight agent**, using the official
[a2a-tck](https://github.com/a2aproject/a2a-tck) (`main`, commit `263b9cf`,
which targets protocol v1.0 — `0.3.x` branches exist and would be the wrong
suite for us). Reports in this folder are the raw output, not a summary.

| Metric | Before | After |
|---|---|---|
| Overall | 19.7% | **39.2%** |
| MUST | 23.8% | **43.5%** |
| SHOULD | 0.0% | **14.3%** |
| Tests | 53 failed / 20 passed | **10 failed / 46 passed** |

## What the TCK found that we could not have

**One missing trailing slash caused 41 of the 53 failures.**

The TCK's JSON-RPC client sets httpx `base_url` to our Agent Card's endpoint
and posts to a relative `"/"`. That resolves to `/a2a/flight_agent/` — with a
trailing slash. We only routed `/a2a/flight_agent`, so the request fell past
the A2A routes into the Flask catch-all mount and came back as a **404 HTML
page**. Every generic client saw a JSON parse error rather than an agent.

No unit test would have produced that URL, because we always constructed it
ourselves. Fixed by serving both forms; pinned by
`test_jsonrpc_answers_on_both_slash_forms`.

A second, related defect surfaced the same way: `combined.py` builds a new
Starlette from `a2a_app.routes`, which silently discards middleware attached to
the inner app. The Agent Card cache headers vanished exactly there.

## The remaining failures, honestly

**Seven of the eight remaining MUST failures share one cause**, and it is a
design decision rather than a defect:

> `CORE-SEND-001`, `CORE-SEND-003`, `CORE-EXECUTION-MODE-001/002`,
> `CORE-MULTI-001a/002a/003`, `JSONRPC-FMT-001`

The TCK drives its generic task-lifecycle tests with **plain text messages**.
Our agent declares `defaultInputModes: ["application/json"]` and rejects text
with `-32005 ContentTypeNotSupported`, because it is a specialist that needs a
validated `TravelRequest`, not a conversational agent.

Rejecting an undeclared input mode with `-32005` is spec-sanctioned — that code
exists for exactly this. But the TCK's lifecycle tests assume an agent that
accepts arbitrary text, so it cannot create a task to test the lifecycle
against, and those requirements fail.

**This is a genuine product question, not a scoreboard one.** Two honest
options:

1. **Leave it.** The restriction is declared on the card, the rejection code is
   correct, and the agent's contract is "send me a structured travel request".
   Ceiling stays around 43% MUST.
2. **Accept a text part** and answer `TASK_STATE_INPUT_REQUIRED` with a message
   naming what is missing. This is the spec's own idiom for "I need more from
   you", would let the lifecycle tests run, and maps onto machinery we already
   have in the orchestrator's intake (`compute_gaps`,
   `clarification_question`). It is real work and widens the agent's contract.

I did **not** make that change unilaterally: widening what the agent accepts to
improve a conformance score, without deciding whether we want that behaviour,
would be optimising the measurement instead of the product.

Remaining non-MUST failures: `CARD-CACHE-002` / `CARD-CACHE-003` (ETag — the
middleware sees a streaming response with no readable body, so it sets
`Cache-Control` but not `ETag`), and `DM-SERIAL-005`.

## Reproducing

The SUT runs against `scripts/ci_stub_provider.py`, so no credentials and no
billed model calls. See `scripts/run_tck_sut.sh`.

```
python scripts/ci_stub_provider.py &
bash scripts/run_tck_sut.sh &
./run_tck.py --sut-host http://127.0.0.1:8080 --transport jsonrpc
```

`A2A_ROOT_AGENT=flight_agent` puts the flight agent at the origin's well-known
path, which is where the TCK looks and without which a run cannot start at all.
Set it to `orchestrator_agent` to certify the front door instead.
