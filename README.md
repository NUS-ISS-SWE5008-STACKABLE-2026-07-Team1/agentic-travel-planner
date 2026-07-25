# Agentic Travel Planner

A documented Flask + LangGraph template using the OpenAI API. It contains five
logical agents: an Orchestrator Agent, Flight Agent, Hotel & Transport Agent,
Accessibility Agent, and Risk & Advisory Agent.

## Architecture

The four specialists run concurrently from a typed shared state. LangGraph waits
at a fan-in barrier, then the orchestrator synthesizes their structured findings.
No agent performs a booking. The starter also has no live supplier/search tools,
so generated prices, availability, advisories, and accessibility claims are
explicitly estimates or verification tasks. Add approved data-provider tools before
using it for real-time decisions.

Each response includes agent findings, concise selection factors, alternatives,
sources, assumptions, limitations, confidence, and safety warnings. This is useful
explainability without storing or exposing private model chain-of-thought.

## Agent and prompt ownership

Each team member can maintain one folder under `flaskapp/travel_ai/agents/`. Every
agent folder contains its execution/tool code in `agent.py` and its model instructions
in `prompt.py`, reducing merge conflicts between team members.

| Owner | Agent folder | Responsibility |
| --- | --- | --- |
| Flight Agent | `agents/flight_agent/` | Flight search and reasoning under arrival-time, schedule, connection, baggage, and budget constraints. |
| Hotel & Transport Agent | `agents/hotel_transport_agent/` | Accommodation and local transit selection compatible with flights and traveller requirements. |
| Accessibility Agent | `agents/accessibility_agent/` | End-to-end accessibility validation, explicit veto warnings, and future bias-audit tooling. |
| Risk & Advisory Agent | `agents/risk_advisory_agent/` | Visa, seasonal, disruption, event, health, and safety risks with high-severity escalation. |
| Orchestrator Agent | `agents/orchestrator_agent/` | Coordination, governance, conflict/escalation handling, and final itinerary synthesis. |

The resulting layout is:

```text
flaskapp/travel_ai/
|-- agents/
|   |-- base.py                    # shared specialist execution and tracing
|   |-- shared.py                  # policy that applies to all five agents
|   |-- flight_agent/
|   |   |-- agent.py
|   |   `-- prompt.py
|   |-- hotel_transport_agent/
|   |   |-- agent.py
|   |   `-- prompt.py
|   |-- accessibility_agent/
|   |   |-- agent.py
|   |   `-- prompt.py
|   |-- risk_advisory_agent/
|   |   |-- agent.py
|   |   `-- prompt.py
|   `-- orchestrator_agent/
|       |-- agent.py
|       `-- prompt.py
|-- graph.py                       # cross-agent workflow and fan-in barrier
|-- schemas.py                     # shared input/output contracts
|-- safeguards.py                  # deterministic assurance rules
`-- tracing.py                     # safe audit metadata
```

An owner normally edits only `agent.py`, `prompt.py`, and tests inside their assigned
agent area. Changes to `agents/base.py`, `agents/shared.py`, `graph.py`, `schemas.py`, `safeguards.py`, or
`tracing.py` affect multiple agents and should be reviewed by the team. In particular,
when a prompt requires a new output field, update the Pydantic contract in
`schemas.py` and add tests before merging.

`agents/__init__.py` is the specialist registry consumed by `graph.py`. Register a
new specialist there and add its allowed name to `schemas.AgentFinding`. The current
graph runs the four specialists in parallel and then runs the orchestrator. The
orchestrator prompt identifies unresolved conflicts for a future negotiation cycle;
an actual retry/negotiation loop must be added in `graph.py` when that feature is
developed.

Prompts alone cannot retrieve live travel facts. Add approved provider calls in the
owning execution module, pass a small and sourced result into the model context, and
retain the shared tracing and safeguards. Keep API keys in environment configuration,
never in prompts or traces.

## Setup

Requires Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Put an OpenAI API key in `.env`, then run `python app.py`. The local address is
`http://127.0.0.1:5000`.

For the bundled demo login, use `demo@example.com` and `TravelDemo2026!`.
Replace `SECRET_KEY`, `LOGIN_EMAIL`, and `LOGIN_PASSWORD_HASH` in `.env` before
deployment. Production systems should replace this demo identity layer with SSO or
another managed identity provider.

## API

Send `POST /api/v1/travel-plans`:

```json
{
  "origin": "Singapore",
  "destination": "Tokyo",
  "departure_date": "2026-10-10",
  "return_date": "2026-10-16",
  "travellers": 2,
  "budget": 4000,
  "currency": "SGD",
  "preferences": ["direct flights", "near public transport"],
  "accessibility_needs": ["step-free route", "wheelchair-accessible room"],
  "risk_tolerance": "low"
}
```

The response's `trace_url` retrieves a tamper-evident JSONL event chain. It stores
agent lifecycle metadata and assurance outcomes, not raw prompts or personal data.
Protect trace access with authorization and a retention policy in production.

## Responsible-AI controls

- Input schemas exclude sensitive traits; unknown fields are rejected.
- Ranking uses stated constraints, with accessibility needs as hard constraints.
- Prompt-injection-like content and oversized inputs are rejected.
- Outputs expose options, factors, uncertainty, provenance, and alternatives.
- Deterministic checks flag missing sources, accessibility review, and alternatives.
- Agent handoffs are request-ID scoped and hash chained for accountability.
- Temperature defaults to zero; the model name is captured in the audit event.

These controls mitigate risk; they do not prove absence of bias. Before production,
add representative evaluation datasets, outcome-parity tests, human escalation,
incident handling, red-team testing, supplier monitoring, authentication, rate
limiting, encrypted trace storage, and a named system owner.

Run tests with `pytest -q`. Tests do not call OpenAI. Pin exact dependency versions
in a generated lock file for production deployment.
