# Claude Code Development Guide

Flask + LangGraph travel planner with five specialist agents. `graph.py` fans out four
specialists in parallel from a typed shared state, joins them at a fan-in barrier, then
runs the orchestrator to synthesize. No agent books anything.

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item .env.secrets.example .env.secrets
python app.py  # http://127.0.0.1:5000
```

`pytest -q` before pushing. Tests never call a live model.

## Agent ownership

One folder per owner under `flaskapp/travel_ai/agents/`. Each has `agent.py` (the
LangGraph node) and `prompt.py` (model instructions).

| Agent | Folder |
| --- | --- |
| Flight | `agents/flight_agent/` |
| Hotel & Transport | `agents/hotel_transport_agent/` |
| Accessibility | `agents/accessibility_agent/` |
| Risk & Advisory | `agents/risk_advisory_agent/` |
| Orchestrator | `agents/orchestrator_agent/` |

Shared, review before changing: `agents/base.py`, `agents/shared.py`, `agents/loop.py`,
`graph.py`, `schemas.py`, `safeguards.py`, `tracing.py`, `a2a.py`, and the
`guardrails/` package.

## Flight Agent

The largest agent by far, and the one whose structure is worth understanding before
editing anything in it.

**The governing idea: code decides which flights, the model only explains the
decision.** `domain.py` searches, filters and ranks real inventory in Python; the model
is handed that finished proposal and asked for prose. Removing the model degrades the
wording, not the candidate list.

### Request flow (`agent.py:292` `flight_node`)

```
A2A request
  -> adapter.to_flight_request        shared TravelRequest -> flight contracts
  -> provider.covers(request)?
       no  -> prompt-only fallback (Path 2, see below)
       yes -> ToolContext / InventoryCache fetches rows (exactly once per request)
              -> mode gate:
                   structured -> reasoning.run_flight_agent
                   agentic    -> agentic.run_agentic_flight_agent
                   auto       -> the loop only if _has_empty_leg (DEFAULT)
              -> propose_flights ranks in code
              -> LLM writes rationale; grounding + output screening enforced
  -> _build_finding -> AgentFinding -> response_message back onto the bus
```

### Modules

| File | Role |
| --- | --- |
| `agent.py` | The graph node. Owns the mode gate, the fallback, and `Option` construction |
| `domain.py` | Pure functions, no I/O, no langchain. Hard filters, ranking, preference-acknowledgment gaps |
| `schemas.py` | Flight contracts. Read the `FlightInventoryItem` and `PreferenceAcknowledgment` docstrings |
| `adapter.py` | The only file that knows both the shared and flight schemas |
| `airports.py` | country + city -> every airport serving that city |
| `guardrails.py` | Input/output screening and flight-ID grounding |
| `reasoning.py` | Single-shot path: tool runs, model explains, retry-then-fallback |
| `tools.py` | `domain.py` exposed as three tools, plus the argument envelope |
| `agentic.py` | The tool-calling subgraph. **The only module here allowed to import langchain** |
| `providers/` | Where inventory comes from — `seed` (default) or `duffel` |
| `seed_data.py` | Static inventory + `seed_data_extended.csv` |

### Things that look like details but are load-bearing

- **`wheelchair_assist_available` and `step_free_boarding` are `bool | None`, and `None`
  means "this supplier does not publish it" — not "unavailable."** Seed rows state a real
  true/false; Duffel has no such field. Never collapse `None` into either boolean.
  `domain.py` treats it as a genuine third case and `agent.py` surfaces it as an explicit
  *unverified* limitation.
- **Hard filters exclude; soft preferences only reorder.** `_screen_item` returns a list
  of reasons (empty = included). `prefer_direct`, `avoid_red_eye` and a non-hard
  `ArrivalPreference` never exclude anything.
- **The rank key is a lexicographic tuple, not a blended score** (`domain.py:173`). Each
  position is individually nameable, which is what lets `_candidate_to_option` restate
  them honestly as `selection_factors`. `cost` is forced terminal so ties always resolve
  deterministically.
- **`PreferenceAcknowledgment.field` is a three-value `Literal`, and that is the actual
  fence.** The model cannot express a request to touch `max_stops`, budget, a hard
  arrival deadline, or accessibility — Pydantic rejects it before `domain.py` sees it.
  `domain.acknowledgment_is_valid()` then re-verifies a real, unanimous gap exists;
  never trust the model's own claim. Acknowledging a preference never produces more or
  different flights — it only discloses that the wish went unmet, which is why it is
  called `acknowledge_unmet_preference` in the tool loop, not "relax."
- **`traveller_genders` reaches the model deliberately**, so the XRAI bias audit can vary
  it. Nothing in ranking reads it and nothing should — `test_flight_bias_audit.py`
  asserts deterministic ranking is byte-identical across genders.

### `FLIGHT_AGENT_MODE`

Resolved once at node construction (`agent.py:290`) so the mode cannot change under a
traveller mid-plan.

- `structured` — always single-shot. The one-variable revert.
- `agentic` — always open the tool loop.
- `auto` — **default.** Single-shot, escalating to the loop only when the deterministic
  search leaves a leg with no candidates.

`auto` exists because of the measurement in `docs/flight_agent/mode-eval.md`: the loop
turned one unstocked-date scenario from 0 options into 6, and produced identical option
counts everywhere else for 2-4 extra seconds. The escalation test (`_has_empty_leg`) is
pure Python over rows already in memory, so the common path keeps single-shot latency.

Loop limits come from `FLIGHT_AGENT_MAX_LLM_TURNS`, `MAX_TOOL_CALLS`,
`MAX_PROVIDER_CALLS`, `LOOP_DEADLINE_SECONDS`, `MAX_DATE_SHIFT_DAYS` (`tools.py:102`).

### What keeps the loop safe (none of it is the prompt)

1. **The argument envelope** (`tools._validate_search_args`) — a searched date must be
   within `MAX_DATE_SHIFT_DAYS` of the **base** request's leg date, measured against the
   base so successive shifts cannot walk outward; searched airports must be a subset of
   what `airports.resolve_route` already resolved. A violating call returns a refusal *to
   the model*, so it can correct itself and the loop survives.
2. **`LoopBudget`** (`agents/loop.py`) — caps model turns, tool calls, provider calls and
   wall clock.
3. **`domain.py` owns ranking.** `rank_flights` permutes precedence between named
   components; it cannot invent one or drop a tiebreaker.
4. **`agent.py` builds every `Option` from `proposal.candidates`**, produced by
   `propose_flights` over rows the cache actually returned. The model's message never
   becomes a flight.

### Path 2: the no-inventory fallback

When nothing covers the route, there is nothing to ground against, so the node falls back
to the shared prompt-only specialist — and `_forbid_concrete_options` (`agent.py:120`)
then **strips every option**, keeping route-level guidance plus a warning.
`validate_grounded_explanation` cannot run on this path (the candidate set it checks
membership against does not exist), and a fabricated option in `options` is what the UI
renders most prominently. `docs/flight_agent/design.md` §3 has the full argument.

Do not reword `ESTIMATE_WARNING` casually: it carries the substring
`safeguards.UNVERIFIED_OPTIONS_MARKER` matches on, and changing it silently stops
`enforce_provenance_disclosure` from firing.

### Inventory

Seed is the default and stays the default — the golden scenarios, the bias audit and
~100 tests are pinned to its exact ranking output, so `FLIGHT_INVENTORY_SOURCE=duffel`
is an explicit opt-in that a stray credential cannot trigger. A missing
`DUFFEL_API_TOKEN` degrades to seed *with a visible note* rather than failing.

Current seed dataset: 1568 rows across 30 airports, departures 2026-08-24 to 2027-01-06.
Regenerate with `scripts/generate_flight_seed_csv.py` — it is deterministic, so a diff
means an input changed. Verify these numbers before quoting them; they move.

## A2A messaging

Every handoff uses the versioned `A2AMessage` envelope from `flaskapp/travel_ai/a2a.py`.
Never assemble one by hand, and never pass a raw dict or free text as a cross-agent
interface. Use `request_message`, `response_message`, or `error_message` — read their
signatures in `a2a.py:71-98` rather than copying a snippet, since all three are
keyword-only and `response_message`/`error_message` take the original `request` (they
derive `correlation_id` from it and reject a sender that was not its recipient).

Adding an agent: register the ID in `AgentId` (`a2a.py`), in `AgentFinding.agent`
(`schemas.py`), and in the registry in `agents/__init__.py`. Define input and output as
strict Pydantic models (`extra="forbid"`). Never put prompts, secrets, credentials,
chain-of-thought, or unnecessary personal data in `payload`, `error`, or traces.

## Guardrails: two layers, don't confuse them

- `flaskapp/travel_ai/guardrails/` — the shared L2 package (`specialist.py` provides
  `make_preflight` / `make_postprocess` used by prompt-only nodes).
- `flaskapp/travel_ai/agents/flight_agent/guardrails.py` — Flight Agent's own screening:
  injection/bias/toxicity on input *before the model sees it*, the same two detectors on
  the model's rationale, and `validate_grounded_ids` for flight-ID grounding.

Bias screening is deliberately two-tier: a protected-attribute mention alone is `medium`
and passes; attribute **plus** a stereotype trigger is `high` and blocks. Travel content
legitimately mentions nationality, religion and culture — only stereotyping is the
problem. Toxicity terms are word-bounded, not substring: a bare `in` test fires "kill" on
Kilimanjaro and "die" on Dieppe.

## Testing

Tests live in `tests/`. Flight coverage is extensive: `test_flight_guardrails.py`,
`test_flight_golden.py` (pinned scenario replays), `test_flight_adapter.py`,
`test_flight_bias_audit.py`, `test_flight_node_providers.py` (pins the single-fetch
invariant), `test_flight_imports.py` (enforces that only `agentic.py` imports langchain).

Golden scenarios are pinned to seed output — a change to ranking or seed data will move
them, and that is the point. Re-pin deliberately, never reflexively.

## Environment

- `.env` — non-secret settings (provider, model, `FLIGHT_AGENT_MODE`, `FLASK_DEBUG`)
- `.env.secrets` — credentials only; both are gitignored, the `.example` files are not
- `LLM_PROVIDER` / `LLM_MODEL` select one provider for all five agents. Supported:
  `openai`, `azure_openai`, `anthropic`, `google`, `deepseek`, `xai`, `meta`,
  `openai_compatible`

## Database

SQLite at `instance/travel_planner.sqlite3` (auto-initialized), or Postgres via
`DATABASE_URL`. Supabase needs the **session pooler** connection string — the direct host
resolves only to IPv6, which Render cannot reach.

`instance/travel_planner.sqlite3` is tracked in git. It is a binary, so a local
modification plus a remote one is a conflict git cannot resolve. Worth untracking.

Trace JSONL files under `TRACE_DIR` sit on Render's ephemeral disk, so
`GET /api/v1/traces/<request_id>` 404s after a redeploy even though the same events
remain in `audit_events`.

## Docs

- `docs/flight_agent/design.md` — the design argument, including Path 2 §3 and the loop §4b
- `docs/flight_agent/mode-eval.md` — the measurement behind `auto`
- `docs/flight_agent/inventory_sources.md` — seed vs. Duffel trade-offs
- `docs/flight_agent/report.md` — team-facing results
- `docs/handoff/` — in-flight handoffs
- `docs/security/` — security drafts and scan notes
- `docs/places_contract.md` — the shared city dataset

## Debugging

`/admin` (gated by `ADMIN_EMAILS`) shows live agent status, latest structured responses
and token usage. Every plan returns a `trace_url` for its tamper-evident JSONL chain.
Useful trace events: `agent_fallback`, `agent_path2_options_stripped`,
`agent_acknowledgment_applied` / `_rejected`, `agent_llm_attempt_failed`,
`agent_tool_rejected`, `agent_budget_exhausted`, `agent_loop_completed`.
