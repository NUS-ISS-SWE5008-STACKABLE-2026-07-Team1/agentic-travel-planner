# Claude Code Development Guide

This is an agentic travel-planning system with five specialist agents that run in parallel. Each agent owner maintains their own folder and can edit independently without merging conflicts.

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item .env.secrets.example .env.secrets
python app.py  # runs on http://127.0.0.1:5000
```

Run tests with `pytest -q` before pushing.

## Agent ownership

Each specialist has its own folder under `flaskapp/travel_ai/agents/`. You can edit `agent.py` and `prompt.py` in your folder without coordinating with others.

| Agent | Folder | Responsibility |
| --- | --- | --- |
| Flight | `agents/flight_agent/` | Flight search and ranking under time, connection, baggage, and budget constraints |
| Hotel & Transport | `agents/hotel_transport_agent/` | Accommodation and local transit compatible with flights |
| Accessibility | `agents/accessibility_agent/` | End-to-end accessibility validation and bias-audit tooling |
| Risk & Advisory | `agents/risk_advisory_agent/` | Visa, seasonal, disruption, health, and safety risks |
| Orchestrator | `agents/orchestrator_agent/` | Coordination and final itinerary synthesis |

Do not edit `agents/base.py`, `agents/shared.py`, `graph.py`, `schemas.py`, or `safeguards.py` without team review — they affect all agents.

## Flight Agent structure

The Flight Agent has a production-ready deterministic layer alongside its prompt-only graph node:

- `domain.py` — searches and ranks real inventory in code; records why each flight was rejected
- `reasoning.py` — asks the LLM only to explain what the code already decided
- `guardrails.py` — validates that the LLM's choices ground to real flights (rejects invented flight IDs)
- `adapter.py` — translates TravelRequest to flight-agent contracts (single point of schema change)

**The graph does not use this layer yet.** `agent.py` still runs prompt-only, so runtime behavior is unchanged. Connecting them is a deliberate reviewed step (change only `create_node`, then add tests).

Inventory: 284 static flights, SIN-origin hub-and-spoke across 18 airports, 2026-08-24 to 2026-10-08. Swap to live Duffel with `FLIGHT_INVENTORY_SOURCE=duffel` and a `DUFFEL_API_TOKEN`.

## Key files to understand

- `flaskapp/travel_ai/a2a.py` — agent-to-agent messaging protocol (v1.0). All handoffs use `A2AMessage` envelopes; never invent your own dictionaries or pass raw text.
- `flaskapp/travel_ai/schemas.py` — shared input/output contracts (Pydantic, `extra="forbid"`). New prompt fields need a schema change here first.
- `flaskapp/travel_ai/graph.py` — cross-agent workflow and fan-in barrier. Currently runs specialists in parallel, then orchestrator. Retry/negotiation loops are future work.
- `flaskapp/travel_ai/safeguards.py` — deterministic assurance rules applied to all agents.
- `flaskapp/travel_ai/tracing.py` — tamper-evident audit events (JSONL + database).
- `flaskapp/places.py` — city → airports mapping (shared with hotel and transport).

Do not touch: `flaskapp/travel_ai/llm.py` (provider abstraction), `flaskapp/travel_ai/cancellation.py` (risk-evaluation helper).

## Common workflows

### Edit your agent's prompt

1. Open `agents/your_agent/prompt.py`
2. Update the instructions
3. Add tests if the new prompt requires new output fields (see schema contract in `schemas.py`)
4. Run `pytest -q`
5. Push

### Add a new output field to your agent

1. Update the Pydantic model in `schemas.py` (e.g., `AgentFinding` or a specialized schema)
2. Add tests covering the new field
3. Update your prompt to produce it
4. Update `agent.py` to extract it
5. Run `pytest -q` before pushing

### Use the Flight Agent's deterministic layer

Replace the prompt-only node with the deterministic one:

1. Open `agents/flight_agent/agent.py`
2. In `create_node`, change the return from `agent.run_flight_agent(...)` to `reasoning.run_flight_agent(adapter.to_flight_request(state, ...))` 
3. Add tests to verify ranking matches your expectations
4. Run `pytest -q` and review the diff
5. Push as a separate commit (it changes what every downstream agent receives)

## Testing

- Tests live in `tests/` and do not call OpenAI (stubbed or seeded).
- Flight tests are extensive: guardrails, accessibility, seat availability, bias audit, golden scenarios.
- Always run `pytest -q` before pushing.
- If a test needs live data, mark it `@pytest.mark.skip("requires live API")` and document why.

Key test files:
- `tests/test_flight_guardrails.py` — verify output validation
- `tests/test_flight_golden.py` — replay known scenarios
- `tests/test_flight_adapter.py` — schema translation
- `tests/test_accessibility_guardrails.py` — accessibility validation

## A2A messaging (agent-to-agent handoff)

All inter-agent communication uses `A2AMessage` envelopes from `a2a.py`. Never pass raw JSON or dicts.

Request:
```python
from flaskapp.travel_ai.a2a import request_message
msg = request_message(
    sender="your_agent",
    recipient="flight_agent",
    correlation_id=request_id,
    payload=travel_request
)
```

Response:
```python
from flaskapp.travel_ai.a2a import response_message
msg = response_message(
    sender="your_agent",
    recipient="orchestrator_agent",
    correlation_id=request.correlation_id,  # copy from request
    payload=finding
)
```

The orchestrator routes these through the graph's `messages` state. Do not invent your own envelope format.

## Environment and secrets

- `.env` — non-secret settings (provider, model, debug flags)
- `.env.secrets` — API keys only (never committed; use `.env.secrets.example` as template)
- `FLASK_DEBUG=true` restarts the server on code changes
- `LLM_PROVIDER` and `LLM_MODEL` select the LLM; all agents use the same provider and model
- Supported providers: `openai`, `azure_openai`, `anthropic`, `google`, `deepseek`, `xai`, `meta`, `openai_compatible`

For local development, use the stubbed flight inventory and skip live API keys.

## Database

- Local: SQLite at `instance/travel_planner.sqlite3` (auto-initialized on startup)
- Production: Postgres via `DATABASE_URL` env var (Supabase: use session pooler, not direct connection)
- Schema: users, travel requests, plans, findings, options, A2A messages (versioned), audit events (hash-chained)
- Migrations: `scripts/migrate_sqlite_to_postgres.py` for one-time moves to Postgres

Note: `instance/travel_planner.sqlite3` is tracked in git but should eventually be untracked. If you modify it locally and a cloud session modifies it remotely, you'll get a merge conflict git can't resolve.

## Deployment

- Render: free tier, ephemeral disk (trace files lost on redeploy)
- DAST scan: only against the live deployment URL, not the staging branch (see `docs/dast-against-the-render-deployment.md`)
- CI pipeline: runs pytest and type checks; no live API calls

## Docs

- `README.md` — overview and setup
- `docs/flight_agent/report.md` — team-facing eval results
- `docs/places_contract.md` — city → airports mapping
- `docs/flight_agent/inventory_sources.md` — seed vs. Duffel trade-offs
- `docs/progress.md` — personal notes (untracked)

## Responsible AI

- Inputs validate schema and reject unknowns
- Accessibility needs are hard constraints, not soft preferences
- Outputs include options, factors, uncertainty, sources, and limitations
- Deterministic checks flag missing sources and alternatives
- Agent handoffs are request-ID scoped and hash-chained
- Temperature is zero; model name is captured in audit events

Before production, add outcome-parity tests, human escalation, incident handling, rate limiting, encrypted trace storage, and a named system owner.

## Debugging

- Admin page at `/admin` (requires `ADMIN_EMAILS` in `.env`) shows live agent status and token usage
- Trace URL in response body is a tamper-evident JSONL event chain
- LangGraph state includes `findings` (synthesis) and `messages` (A2A envelopes)
- Flight agent can swap inventory sources via env var for testing Duffel vs. seed

## Quick links

- Flight Agent test scenarios: `scripts/demo_golden_scenario.py`, `scripts/demo_multi_gap_relaxation.py`
- Flight inventory regeneration: `scripts/generate_flight_seed_csv.py` (deterministic, diffs mean inputs changed)
- Schema validation: `flaskapp/travel_ai/schemas.py`
- Guardrails: `flaskapp/travel_ai/agents/flight_agent/guardrails.py` and `safeguards.py`
