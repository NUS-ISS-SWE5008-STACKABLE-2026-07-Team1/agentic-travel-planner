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

## Where to build agents and prompts

The main AI code is under `flaskapp/travel_ai/`:

| File | What to add or change |
| --- | --- |
| `prompts.py` | Start here. Edit the shared policy, four specialist prompts, and orchestrator prompt. |
| `agents.py` | Add agent execution logic and calls to approved flight, hotel, map, weather, or advisory APIs. |
| `graph.py` | Change agent order, parallel branches, conditional routing, and LangGraph edges. |
| `schemas.py` | Define the structured information each specialist and final plan must return. |
| `safeguards.py` | Add deterministic validation, fairness, business, and human-review rules. |
| `tracing.py` | Add safe audit metadata. Do not store API keys or private chain-of-thought. |
| `service.py` | Configure the OpenAI model and invoke the compiled graph. |

Search the source for `CUSTOMIZE` or `ADD` to find the marked extension points.
For most prompt-only changes, edit `prompts.py` and restart Flask. If a prompt asks
for a new output field, also add that field to the relevant model in `schemas.py`.

Example specialist prompt:

```python
"flight_agent": """Compare verified flight options from the supplied provider data.
Prioritize the user's stated budget and schedule. Return at least two alternatives,
the source URL, price timestamp, baggage assumptions, and connection risks.
Never invent a flight number, price, or availability.""",
```

Prompts alone cannot retrieve live travel facts. Implement provider calls in
`agents.py`, pass only the relevant results into the message, cite their source and
timestamp, and retain the existing safeguards and trace events.

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
