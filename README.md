# Agentic Travel Planner

A documented Flask + LangGraph template using the OpenAI API. It contains five
logical agents: an Orchestrator Agent, Flight Agent, Hotel & Transport Agent,
Accessibility Agent, and Risk & Advisory Agent.

## Architecture

The four specialists run concurrently from a typed shared state. LangGraph waits
at a fan-in barrier, then the orchestrator synthesizes their structured findings.
No agent performs a booking. Flight inventory can optionally come from Duffel,
and the Accessibility Agent can retrieve live web evidence through Serper. Other
generated prices, availability, and advisories remain estimates or verification
tasks until an approved provider is connected. Retrieved accessibility evidence is
still advisory and must be verified with the relevant supplier before booking.

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
| Accessibility Agent | `agents/accessibility_agent/` | Privacy-safe requirement planning, Serper web evidence, provenance enforcement, deterministic ratings/vetoes, and supplier-verification questions. |
| Risk & Advisory Agent | `agents/risk_advisory_a1gent/` | Visa, seasonal, disruption, event, health, and safety risks with high-severity escalation. |
| Orchestrator Agent | `agents/orchestrator_agent/` | Coordination, governance, conflict/escalation handling, and final itinerary synthesis. |

The resulting layout is:

```text
flaskapp/travel_ai/
|-- agents/
|   |-- base.py                    # shared specialist execution and tracing
|   |-- shared.py                  # policy that applies to all five agents
|   |-- flight_agent/
|   |   |-- agent.py               # LangGraph node (what the graph runs today)
|   |   |-- prompt.py
|   |   |-- adapter.py             # TravelRequest <-> Flight Agent contracts
|   |   |-- airports.py            # country + city -> airport resolution
|   |   |-- schemas.py             # flight-specific contracts
|   |   |-- domain.py              # deterministic search/filter/rank
|   |   |-- guardrails.py          # input/output screening and grounding
|   |   |-- reasoning.py           # LLM layer over the deterministic tool
|   |   |-- seed_data.py           # static inventory (+ seed_data_extended.csv)
|   |   `-- providers/             # where inventory comes from
|   |       |-- base.py            # InventoryProvider protocol + InventoryResult
|   |       |-- seed.py            # the static dataset (default)
|   |       `-- duffel.py          # live Duffel supplier search (opt-in)
|   |-- hotel_transport_agent/
|   |   |-- agent.py
|   |   `-- prompt.py
|   |-- accessibility_agent/
|   |   |-- agent.py               # guarded LangGraph specialist node
|   |   |-- prompt.py              # evidence, rating and reflection policy
|   |   |-- models.py              # typed requirements, search plans and evidence
|   |   |-- planning.py            # requirement extraction + privacy-safe queries
|   |   |-- retrieval.py           # Serper search, retries and source metadata
|   |   `-- guardrails.py          # input/output screening, citations and vetoes
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

### Accessibility Agent

The Accessibility Agent turns each traveller's accessibility needs into a bounded,
privacy-conscious evidence review. It does not diagnose a disability, make a booking,
or treat a general accessibility label as proof that an option is suitable.

Its runtime flow is:

1. `planning.py` reads per-traveller and legacy aggregate accessibility needs,
   deduplicates them, and classifies them as mobility, vision, hearing, cognitive,
   service-animal, medical-equipment, dietary, or other requirements.
2. It builds up to four searches using the destination and normalized functional
   category. Raw medical details and free-text requirements are not sent to Serper.
3. `retrieval.py` calls `https://google.serper.dev/search`, requesting at most four
   results per query and eight results for the complete agent run. Transient network
   failures are retried once.
4. Each accepted HTTPS result receives an evidence ID (`E1`, `E2`, ...), title,
   excerpt, URL, query scope, retrieval time, and provenance classification:
   official, specialist, crowdsourced, or unknown.
5. `guardrails.py` screens retrieved titles/excerpts as untrusted content. Generated
   claims must cite an evidence ID and its exact retrieved URL. Fabricated, malformed,
   insecure, or unmatched links are removed.
6. Unknown sources and unsupported claims are marked unverified and cannot receive a
   high deterministic accessibility rating. Factual content is withheld when it
   claims web support but has no valid reference link.
7. Options marked `Status: unmet` are removed by the agent and recorded in an
   `ACCESSIBILITY VETO` warning. Missing evidence produces precise questions for the
   airport, transport operator, hotel, venue, or other supplier.
8. Vetted URLs are copied into the final plan even if the orchestrator omits them.
   The chatbot presents each Accessibility Agent option with status, rating, evidence
   details, limitations, website hostname, full URL, and a safe clickable verification
   link.

The agent reviews five journey segments: arrival/airport, local transport,
accommodation, activities/public spaces, and departure/connections. A source can
support a general policy without proving that a particular hotel room, vehicle,
station, or venue satisfies the traveller's requirements; those gaps remain explicitly
unverified.

Focused verification:

```powershell
python -m pytest -q tests/test_accessibility_planning.py `
  tests/test_accessibility_retrieval.py `
  tests/test_accessibility_guardrails.py `
  tests/test_accessibility_evidence_delivery.py
```

### Flight Agent's deterministic layer

Flight Agent carries a second, fuller implementation alongside its prompt-only
graph node. `domain.py` searches, filters and ranks real inventory in code, and
records why every rejected flight was rejected (`screen_flights`); `reasoning.py`
then asks the model only to explain what the code already decided, with
`guardrails.validate_grounded_explanation` rejecting any flight ID the model
invents. `adapter.py` translates the shared `TravelRequest` into these contracts
and is the single place that knows both schemas — point changes there when the
shared schema or the intake form moves.

**The graph does not use this layer yet.** `agent.py` still runs the prompt-only
node, so runtime behaviour is unchanged. Connecting them is one change to
`create_node` (`adapter.to_flight_request` produces what
`reasoning.run_flight_agent` needs), deliberately left as its own reviewed step
because it changes what every downstream agent receives.

One limit to know before wiring it in: by default inventory is 284 static rows,
SIN-origin hub-and-spoke across 18 airports between 2026-08-24 and 2026-10-08,
so anything else correctly returns no candidates.

The intake form collects a country **and a city**, and a city resolves to every
airport serving it — picking Tokyo ranks Haneda and Narita together rather than
silently meaning Narita. The dataset (`flaskapp/places.py`, 253 cities across 61
countries) is shared with the other agents, so hotel and transport inventory can
be keyed on the same cities; see
[docs/places_contract.md](docs/places_contract.md).

`domain.py` takes inventory as a plain argument and never fetches it, so the
source is swappable. `providers/` holds that seam: `seed` is the default (and
what the golden scenarios are pinned to), and `FLIGHT_INVENTORY_SOURCE=duffel`
with a `DUFFEL_API_TOKEN` swaps in a live Duffel supplier search. Duffel
publishes no accessibility or seat-availability data, which is handled as an
explicit "unverified" third state rather than guessed either way — see
[docs/flight_agent/inventory_sources.md](docs/flight_agent/inventory_sources.md)
for the full trade-off table and setup steps.

Two demo scripts exercise it against a live model:

```powershell
python scripts/demo_golden_scenario.py        # the wheelchair/SIN->Tokyo scenario
python scripts/demo_multi_gap_relaxation.py   # does relaxation choice track party context?
```

`scripts/generate_flight_seed_csv.py` regenerates the extended inventory
deterministically — same output every run, so a regeneration that produces a
diff means an input changed.

## Internal agent handoff envelope

Every agent handoff uses the versioned `A2AMessage` envelope defined in
`flaskapp/travel_ai/a2a.py`. Agents must not invent their own dictionaries or pass
unstructured text as a cross-agent interface. This is the application's local,
transport-neutral LangGraph contract; it is not itself the official Agent2Agent
wire protocol. `flaskapp/travel_ai/a2a_standard.py` adapts this local contract to
official A2A 1.x messages, tasks, artifacts, Agent Cards, and JSON-RPC endpoints.

Required envelope fields:

| Field | Standard |
| --- | --- |
| `protocol_version` | Currently `"1.0"`; change only through a reviewed protocol release. |
| `message_id` | A new UUID for each message. Never reuse it for a retry. |
| `correlation_id` | The root travel-request UUID, unchanged across the full workflow. |
| `sender`, `recipient` | Registered agent IDs from `AgentId` in `a2a.py`. They must differ. |
| `message_type` | `request`, `response`, `event`, or `error`. |
| `status` | `pending` for requests, `completed` for successful responses, `failed` for errors. |
| `created_at` | Timezone-aware ISO 8601 timestamp in UTC. |
| `payload_type` | Name of the payload schema, such as `TravelRequest` or `AgentFinding`. |
| `payload` | JSON object validated by the schema named in `payload_type`. |
| `error` | `null` except for errors; errors include `code`, safe `message`, `retryable`, and optional `details`. |

Canonical request:

```json
{
  "protocol_version": "1.0",
  "message_id": "ce57f334-0768-4dd7-b03a-95bc096f0360",
  "correlation_id": "86754012-a98f-4e68-a41b-7990cdd8f378",
  "sender": "orchestrator_agent",
  "recipient": "flight_agent",
  "message_type": "request",
  "status": "pending",
  "created_at": "2026-07-27T08:00:00Z",
  "payload_type": "TravelRequest",
  "payload": {"origin": "Singapore", "destination": "Tokyo"},
  "error": null
}
```

Canonical successful response (the `payload` must contain the complete validated
`AgentFinding`, abbreviated here only for readability):

```json
{
  "protocol_version": "1.0",
  "message_id": "aa18fd15-43ec-4476-9f47-45e00fcadc4e",
  "correlation_id": "86754012-a98f-4e68-a41b-7990cdd8f378",
  "sender": "flight_agent",
  "recipient": "orchestrator_agent",
  "message_type": "response",
  "status": "completed",
  "created_at": "2026-07-27T08:00:02Z",
  "payload_type": "AgentFinding",
  "payload": {"agent": "flight_agent", "summary": "...", "options": [], "warnings": [], "confidence": 0.8},
  "error": null
}
```

Agent developer checklist:

1. Add the new ID to `AgentId` in `a2a.py`, to `AgentFinding.agent` in `schemas.py`,
   and to the registry in `agents/__init__.py`.
2. Define input and output as strict Pydantic models (`extra="forbid"`). Use stable
   schema names in `payload_type`; additive optional fields are backward-compatible,
   while renamed/removed/required fields need a protocol-version change.
3. Create messages with `request_message`, `response_message`, or `error_message`.
   Do not manually assemble envelopes. A responder must copy the request's
   `correlation_id`, address the response to the original sender, and create a fresh
   `message_id`.
4. Validate before processing and before sending. Do not place prompts, secrets,
   chain-of-thought, credentials, or unnecessary personal data in `payload`, `error`,
   or traces. Use an error `code` for program logic; keep its message safe to log.
5. Add contract tests covering valid request/response handling, rejected unknown
   fields, incorrect routing, and failure semantics. Run `pytest -q` before merging.

Protocol `1.0` currently routes `TravelRequest` inputs from the orchestrator to each
specialist and returns `AgentFinding` outputs. LangGraph retains these envelopes in
the typed `messages` state while the existing `findings` state supports synthesis.

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
Copy-Item .env.secrets.example .env.secrets
```

Choose the provider and model in `.env`; these are non-secret settings. With
`LLM_PROVIDER=auto`, exactly one provider credential in `.env.secrets` is detected.
`LLM_MODEL` may be left blank to use the application's provider default, but pinning
it is recommended for reproducible behavior:

```dotenv
# Azure OpenAI
LLM_PROVIDER=azure_openai
LLM_MODEL=my-gpt-5-deployment
AZURE_OPENAI_API_VERSION=2024-12-01-preview

# Or OpenAI
# LLM_PROVIDER=openai
# LLM_MODEL=gpt-5
```

Supported provider values are `azure_openai`, `openai`, `anthropic`, `google`,
`deepseek`, `xai`, `meta`, and `openai_compatible`. All five agents use the same
selected provider and model. DeepSeek and xAI use their official OpenAI-compatible
endpoints. `meta` represents a hosted Meta model: set `META_BASE_URL` to the hosting
vendor's OpenAI-compatible endpoint and set `LLM_MODEL` to that vendor's model ID.
Use `openai_compatible` with `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` for other
compatible resources. If multiple credentials are present, choose `LLM_PROVIDER`
explicitly to avoid ambiguity.

Put only the matching credentials in `.env.secrets`. Both `.env` and `.env.secrets`
are gitignored, while their `.example` templates are safe to commit. Production
deployments should inject the same variables through their hosting platform's secret
manager instead of creating files. Process environment variables take precedence over
local files. The old misspelled `crediential.env` remains readable for backward
compatibility but should not be used for new setups.

Run `python -m scripts.run_all` after configuration to start both the website and
the official A2A endpoints at `http://127.0.0.1:5000`. `python app.py` remains
available when only the Flask website is needed, but it does not expose Agent
Cards or A2A JSON-RPC routes. Never paste credentials into prompts, logs, source
code, or Git. If a key is exposed, revoke and replace it with the provider
immediately.

### Accessibility Agent web evidence

The Accessibility Agent can use Serper to find current evidence related to each
traveller's functional accessibility requirements. Create a key at `serper.dev`,
then place it in the ignored `.env.secrets` file:

```dotenv
SERPER_API_KEY=your-serper-key
```

The key is read only by `accessibility_agent/retrieval.py` and is sent in the
Serper `X-API-KEY` request header; it is never included in search text, model
prompts, stored evidence, or chatbot output. Without the key, planning continues
with an explicit “accessibility evidence unavailable” warning. Search queries use
the destination and normalized functional categories rather than raw medical
details. Returned HTTPS pages are treated as untrusted evidence, screened by the
agent guardrails, cited by URL, and labelled as official, specialist,
crowdsourced, or unknown provenance.

For local development, set `FLASK_DEBUG=true` in the ignored `.env` file. Running
`python app.py` then automatically restarts the server when application code or
templates change. Keep debug mode disabled in production.

## Local database

The application initializes `instance/travel_planner.sqlite3` automatically on
startup. SQLite requires no separate database server. To initialize it explicitly:

```powershell
$env:FLASK_APP = "app.py"
flask init-db
```

Set `DATABASE` in `.env` to use another local path. The schema stores users, travel
requests and completed plans, specialist findings and options, versioned A2A
messages, and hash-chained audit events. List fields are encoded as JSON, while
relationships and frequently queried identifiers remain normalized and indexed.
The configured demo user is inserted only when its email does not already exist,
so startup never overwrites a changed password.

Users can create an account at `/register` with their name, email, country, and
birthday. Passwords must contain at least 12 characters with uppercase, lowercase,
a number, and a special character. Passwords are stored only as Werkzeug hashes;
email addresses are case-insensitively unique.

## Hosted database

Set `DATABASE_URL` to a Postgres DSN and the application uses it instead of
SQLite; leave it unset and local development is unchanged. Supabase users must
use the **session pooler** connection string — its host contains
`pooler.supabase.com` — not the direct connection, whose host resolves only to
an IPv6 address that Render cannot reach. Percent-encode any `/`, `@`, `#` or
`?` in the password before putting it in the DSN.

Creating the `citext` extension the schema relies on for case-insensitive
email uniqueness (`CREATE EXTENSION IF NOT EXISTS citext;`) requires a role
with sufficient privilege; the default Supabase `postgres` role has it, but a
restricted application role may not.

Copy the existing local data across once:

```powershell
python scripts/migrate_sqlite_to_postgres.py --destination $env:DATABASE_URL --dry-run
python scripts/migrate_sqlite_to_postgres.py --destination $env:DATABASE_URL
```

The script is idempotent and prints per-table counts, exiting non-zero if any
table ends up short. It also refuses to run against a destination that
already has rows in any of the migrated tables, to avoid silently dropping
data under `ON CONFLICT DO NOTHING`; pass `--allow-nonempty` only for a
deliberate resume of a partial migration.

Moving state into Supabase does not make the deployment fully stateless.
`flaskapp/travel_ai/tracing.py` still writes each request's tamper-evident
audit trace as a JSONL file under `TRACE_DIR` (default
`instance/traces`) on the instance's local filesystem, in addition to writing
the same events to the `audit_events` table. On Render's free tier that
directory sits on the ephemeral disk, so every deploy and every spin-down
still wipes those trace files — the `GET /api/v1/traces/<request_id>`
endpoint (the `trace_url` returned with every plan) reads only from the
JSONL file, not from `audit_events`, so it will 404 for any request whose
trace file was lost even though the same events are still durable in the
database. Supabase resolves this for users, plans,
and audit rows; it does not resolve it for trace-file retrieval unless
`TRACE_DIR` is also moved onto persistent or external storage.

## Administrator monitoring

Set `ADMIN_EMAILS` in `.env` to a comma-separated list of registered accounts that
may access `/admin`, for example `ADMIN_EMAILS=admin1@example.com,admin2@example.com`.
The older single `ADMIN_EMAIL` setting remains supported.
The administrator page refreshes every two seconds and shows recent user requests,
live agent status, each agent's latest structured response, and Azure-reported input,
output, and total token usage. Monitoring data is recorded for new requests after
this feature is enabled; older trace-only requests do not contain token metadata.

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
  "traveller_ages": [34, 32],
  "traveller_genders": ["male", "female"],
  "traveller_accessibility_needs": [[], ["step-free access"]],
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

## Agent2Agent (A2A) interoperability

The application supports the official A2A 1.x protocol through the official
Python SDK. The existing LangGraph fan-out/fan-in workflow remains the default
internal execution path, while an adapter makes each specialist independently
discoverable and callable by a standards-compliant external orchestrator. The
same agent implementation, Pydantic validation, guardrails, evidence handling,
database recording, and audit tracing are reused on both paths.

The implementation consists of:

- `flaskapp/travel_ai/a2a_standard.py`: Agent Cards, request conversion,
  `SpecialistAgentExecutor`, task status, artifacts, failures, and cancellation.
- `scripts/a2a_server.py`: standalone A2A-only ASGI service.
- `flaskapp/combined.py` and `asgi.py`: one ASGI application containing the
  Flask website and all A2A routes.
- `scripts/run_all.py`: local one-command launcher with the correct advertised
  Agent Card URL.

### Recommended local startup

Install the dependencies once, then start the complete application:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m scripts.run_all
```

This single process serves both interfaces on port 5000:

| Interface | URL |
| --- | --- |
| Web application | `http://127.0.0.1:5000/` |
| Flight Agent Card | `http://127.0.0.1:5000/a2a/flight_agent/.well-known/agent-card.json` |
| Hotel & Transport Agent Card | `http://127.0.0.1:5000/a2a/hotel_transport_agent/.well-known/agent-card.json` |
| Accessibility Agent Card | `http://127.0.0.1:5000/a2a/accessibility_agent/.well-known/agent-card.json` |
| Risk Advisory Agent Card | `http://127.0.0.1:5000/a2a/risk_advisory_agent/.well-known/agent-card.json` |

Do not run `python app.py` at the same time. If the Agent Card returns Flask's
“Not Found” page, stop the Flask-only process with `Ctrl+C` and start
`scripts.run_all` instead. Stop the combined server with `Ctrl+C`.

### Standalone A2A service

To run only the A2A agents, without the website, use:

```powershell
.\.venv\Scripts\python.exe -m scripts.a2a_server
```

The standalone service also defaults to `http://127.0.0.1:5000`. Configure
`A2A_HOST`, `A2A_PORT`, and the externally reachable `A2A_BASE_URL` when those
defaults are unsuitable.

### A2A request and response contract

Each Agent Card advertises A2A 1.0 over the `JSONRPC` protocol binding with
`application/json` input and output modes. Requests contain exactly one JSON
data Part holding a validated `TravelRequest`, either directly or under a
`travel_request` property. A successful task produces an `agent-finding`
artifact containing the validated `AgentFinding`, followed by a completed task
status. Invalid requests and execution errors produce a failed task status;
cancellation produces a cancelled task status.

### Single-process deployment

`asgi.py` is the production entry point for a single deployment. It registers
the A2A routes before mounting the Flask WSGI application as the fallback. A
compatible start command is:

```text
uvicorn asgi:application --host 0.0.0.0 --port $PORT --workers 1
```

Set `A2A_BASE_URL` to the deployment's public HTTPS origin so Agent Cards do not
advertise a local address. Keep one worker until the in-memory planning-job
registry is moved to shared storage. The A2A endpoints do not yet implement
application authentication, so add TLS, authentication, authorization, rate
limiting, and request-size controls at the deployment boundary before exposing
them publicly.

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
