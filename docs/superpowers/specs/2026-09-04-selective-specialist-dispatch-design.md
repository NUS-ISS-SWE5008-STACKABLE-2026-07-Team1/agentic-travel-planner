# Selective specialist dispatch

Status: designed
Date: 2026-09-04

## Problem

Every planning run invokes all four specialists. `graph.py` adds a `START` edge
to each of them unconditionally, and `service.py:80-89` emits one A2A request
message per name in `SPECIALISTS`. A traveller who asks only for a hotel still
pays for a flight search, waits on it — the barrier edge means synthesis starts
only after the slowest branch — and receives a plan discussing flights they did
not ask about.

The comment in `graph.py` has anticipated this since it was written: *"Add
conditional edges here if an agent should run only for certain requests."*
Nothing has.

## Scope

In scope: letting the traveller state whether they want flights, hotel, or both,
and dispatching only the specialists that serves.

Out of scope, explicitly:

- **Relaxing `TravelRequest`.** A hotel-only request still supplies origin,
  destination and both dates. `origin`/`destination` are NOT NULL in
  `travel_requests`, the admin dashboard queries them, and Risk & Advisory
  reasons at country granularity. Making origin optional touches the schema, a
  migration, the dashboard and one agent's assumptions; it is a larger change
  that deserves its own design. Hotel search needs the dates regardless.
- **Letting the traveller switch off Risk & Advisory or Accessibility.** See
  "Decisions".
- **An orchestrator-driven supervisor that chooses agents itself.** A design for
  that exists on `subbu-25Aug` (`e382b21`, `80051ef`) and is not in `release`.
  It was considered and set aside: which agents run becomes non-deterministic,
  and a wrong call silently drops a specialist the traveller needed.

## Decisions

| Decision | Choice | Rejected alternative |
| --- | --- | --- |
| Who decides | The traveller, explicitly | The orchestrator model inferring it |
| Where the choice lives | `TravelRequest.plan_scope` | A separate intent object |
| Shape | One `Literal["both","flights","hotel"]` | A list of agent names |
| Dispatch mechanism | Compose the graph from the selected set | Conditional edges plus a router |
| Trip brief | Unchanged; scope controls dispatch only | Trimming the questions too |
| Switchable agents | Flights and hotel only | All four |
| Absent scope | Fall back to `both` | Require it of every caller |

Three of these earn an explanation.

**Compose, don't route.** The barrier edge
`workflow.add_edge(list(SPECIALISTS), "orchestrator_agent")` joins on a fixed
source list, and a join whose sources never execute does not fire. Keeping every
node and routing around some of them would mean replacing that join with
different machinery to preserve a graph shape that is rebuilt per request
anyway. Building the graph from the selected set keeps the join correct by
construction, and makes the compiled graph an honest description of what this
request actually did.

**Risk & Advisory always runs; Accessibility runs when needs exist.** Neither is
politeness. Visa rules and safety advisories are not something a traveller
should be able to switch off without being told what they lose. And
`safeguards.assess_plan` warns when `accessibility_needs` are present and no
accessibility finding reaches it — skipping that agent while needs are stated
would ship a plan that ignores a stated requirement and then complain about
itself.

**An absent scope means `both`.** `both` is the superset, so a request that
never states a scope — every stored row, every golden scenario, any API caller
written before this field existed — runs an agent the traveller may not have
needed. That is the harmless direction: the opposite failure silently drops a
specialist, and the traveller cannot tell from a plan that flights were never
searched.

Note this is about an ABSENT value, not a malformed one. A `plan_scope` the
enum does not recognise is rejected by `TravelRequest` at L0 like any other bad
value, so `specialists_for` never sees one — it takes a validated request.

## Flow

```
traveller prompt
  │
  ├─ extraction reads an explicit scope ("book me a hotel") ──► plan_scope
  │      not stated → gap question: Flights and hotel / Flights only / Hotel only
  │
  ▼
TravelRequest.plan_scope
  │
  ▼
specialists_for(request) ── pure function, no model
  │   flights   ⟵ plan_scope in {both, flights}
  │   hotel     ⟵ plan_scope in {both, hotel}
  │   risk      ⟵ always
  │   access    ⟵ any accessibility need stated
  │
  ├─► service.py: one A2A request message per SELECTED agent
  └─► build_travel_graph(..., specialists=selected)
          nodes, START edges and the barrier all built from the same set
  │
  ▼
orchestrator synthesises from the findings that arrive
  │
  ▼
plan.limitations gains "Not consulted: flight_agent." (deterministic)
```

## Components

### `flaskapp/travel_ai/schemas.py`

```python
plan_scope: Literal["both", "flights", "hotel"] = "both"
```

Defaulted, so every existing caller, stored row, golden scenario and bias-audit
fixture stays valid without being touched — the same additive discipline the
city fields used.

A single value rather than a list of agent names. The traveller thinks in trips,
not in agents; a list would also let a caller name `accessibility_agent`
directly, which the rules below are meant to decide.

### `flaskapp/travel_ai/dispatch.py` (new)

```python
ALWAYS: frozenset[str] = frozenset({"risk_advisory_agent"})

def specialists_for(request: TravelRequest) -> tuple[str, ...]
```

Pure: a request in, a stable ordered tuple of agent names out. No model, no IO,
no config, so the whole dispatch decision is testable in one place without
building a graph. Order is `SPECIALISTS` order, so traces stay comparable
between runs.

`accessibility_agent` is included whenever `accessibility_needs` or any entry in
`traveller_accessibility_needs` is non-empty, regardless of `plan_scope`.

### `flaskapp/travel_ai/graph.py`

`build_travel_graph(llm, tracer, cancel_event=None, guardrail=None,
specialists=SPECIALISTS)`. The loop, the `START` edges and the barrier edge all
read that one argument. The default preserves every existing caller and test.

### `flaskapp/travel_ai/service.py`

Computes `selected = specialists_for(request)` once, passes it to
`build_travel_graph`, and builds A2A request messages only for those names. Both
must come from the same value: `make_specialist_node` raises
`Missing A2A request for {name}` when a node runs without one, so a graph and a
message list that disagree is a crash, not a degraded plan.

Records `specialists_dispatched` on the tracer with the selected names and the
scope, so the audit trail says who was asked and why.

### `flaskapp/travel_ai/safeguards.py`

`disclose_unconsulted(plan, selected)` appends one line to `plan.limitations`
naming the specialists that did not run. Modelled on
`enforce_provenance_disclosure` and for the same reason: absence is not a
signal a traveller can read, and the orchestrator cannot be relied on to
mention an agent that produced nothing. Called from `service.py` beside the
existing `merge_accessibility_sources`.

### Intake

`InputKind` gains `"scope"`. `SCALAR_FIELDS` gains
`("plan_scope", "What should we plan?", "scope", None)`, asked only when the
extraction did not read it from the prompt. `intake.js` renders it through the
existing select branch that already serves `country` and `gender`, with three
fixed options.

The extraction prompt gains one rule: record `plan_scope` when the traveller
says what they want ("book me a hotel" → `hotel`), and leave it null otherwise.
Reading what they wrote is comprehension; deciding for them is not.

**Known friction:** this adds a question to conversations that never mentioned
scope, which is the common case. Defaulting silently to `both` would avoid it
and never drop an agent, but then the traveller never actually chooses, which is
the property this design exists to provide. Asking is the deliberate choice.

### Persistence

One `ALTER TABLE travel_requests ADD COLUMN plan_scope TEXT NOT NULL DEFAULT
'both'` in `initialize()`, following the `origin_city`/`destination_city` block
rather than the JSON-list loop above it, whose `DEFAULT '[]'` is wrong here.
`save_plan`'s INSERT gains the column.

## Error handling

| Condition | Behaviour |
| --- | --- |
| `plan_scope` absent | Defaults to `both`; all four dispatched |
| `plan_scope` unrecognised | Rejected by `TravelRequest` at L0, as any bad enum is |
| Scope excludes hotel, needs stated | Accessibility still runs; hotel still skipped |
| Every selectable agent excluded | Impossible: Risk & Advisory always runs, so the graph always has at least one specialist and the barrier always has a source |
| Graph and message list disagree | Cannot happen — both derive from one `specialists_for` call |

## Known limitations

- **Scope is per request, not per refinement.** Refining resubmits the whole
  request, so changing scope means changing it in the trip details rather than
  asking for it in the refinement box.
- **A hotel-only plan still costs a full `TravelRequest`.** The traveller
  supplies an origin country they may not need. Relaxing that is the follow-on
  change named in Scope.
- **The orchestrator prompt is unchanged.** It already synthesises from whatever
  findings arrive, but it has never been evaluated on a two-finding input; the
  disclosure line is what guarantees the omission is visible regardless.

## Testing

- `specialists_for` across the matrix: each scope value, with and without
  accessibility needs, including that stated needs override a hotel-only scope
  and that Risk & Advisory appears in every result.
- An absent `plan_scope` yields all four, so existing golden scenarios and the
  bias audit dispatch exactly as they do today.
- `build_travel_graph` with a restricted set compiles, contains no node for the
  excluded agent, and still reaches the orchestrator — the barrier fires.
- `service.py` builds A2A messages for exactly the selected names.
- End to end with the model stubbed: a hotel-only request produces no
  flight finding and a plan whose limitations name `flight_agent`.
- Intake: `plan_scope` is asked when unstated, not asked when the extraction
  read it, and survives `merge_answers` into `to_request_payload`.
