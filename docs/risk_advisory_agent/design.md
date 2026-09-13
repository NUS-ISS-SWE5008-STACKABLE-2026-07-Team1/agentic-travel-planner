# Risk & Advisory Agent — Design

Describes the agent as it is built today. Sources: `flaskapp/travel_ai/
agents/risk_advisory_agent/` (adapter.py, schemas.py, providers/, domain.py,
reasoning.py, guardrails.py, prompt.py, agent.py), `flaskapp/database.py`,
and `flaskapp/travel_ai/agents/flight_agent/` for the pattern this mirrors.

---

## 1. The core architecture: the tool decides, the model narrates

Same governing idea as Flight and Hotel: `domain.py` queries a grounded set
of facts in plain Python; the model is handed that finished list and asked
only to prioritise, connect, and narrate it. Removing the model degrades the
wording, not the underlying facts.

```
providers/     DatabaseRiskProvider          reads the reference tables
   ↓
domain.py      propose_risks()               query + date filter — deterministic
   ↓
reasoning.py   run_risk_agent()              the LLM narrates OVER that output
   ↓                                          grounding + escalation enforced
guardrails.py  screen_input / screen_output  this agent's own, not borrowed
```

The consequence that matters: if the LLM call fails or fails a check twice,
`_fallback_response` renders the tool's own items with no narrative
(`reasoning.py`). The agent degrades to workflow-level reliability rather
than blocking, exactly like Flight/Hotel's `_fallback_response`.

---

## 2. Reference data: three tables, grouped by shape

`flaskapp/database.py` defines three tables — `risk_standing_facts`,
`risk_seasonal_windows`, `risk_dated_events` — grouped by how they are
queried, not by which of the ~18 advisory categories (visa, local laws,
cultural norms, currency/customs, cybersecurity, crime, scams, political
stability, traveller-group risk, emergency numbers, seasonal weather, local
events, public holidays, labour action...) a given row belongs to:

| Table | Shape | Queried by |
|---|---|---|
| `risk_standing_facts` | True regardless of travel dates | destination alone |
| `risk_seasonal_windows` | Recurring, month-bound | destination + month overlap |
| `risk_dated_events` | A specific date range | destination + date-range overlap |

`category` is a free-text column, not an enum or a per-category table, so a
new advisory category is an inserted row, never a migration.

`seed_data.py` populates all three for the five destinations the team
demos and tests against — Singapore, Berlin, Tokyo, Barcelona, and
Washington, D.C. — researched from real government and travel-advisory
sources, then written as illustrative reference data: every row's `source`
column says so explicitly, and that string is never dropped downstream.
Several dates deliberately overlap a seasonal window (Tokyo's Obon holiday
inside typhoon season and summer heat; Washington's Independence Day inside
hurricane-remnant season) so `reasoning.py` has real, data-backed cases for
connecting more than one risk into a single insight, guarded by
`tests/test_risk_advisory_seed_data.py` so an edit to the dates can't
silently break the overlap.

**A stated assumption:** the traveller's *origin country* stands in for
their *passport nationality* when reasoning about visas — the two aren't
always the same (someone can fly from Singapore on a different country's
passport), and `TravelRequest` has no separate nationality field.

---

## 3. Grounding: `risk_id` and the checks around it

Every `RiskItem` `domain.py` produces carries a `risk_id` derived from the
destination and the fact's own content — `fact-<destination>-<category>`,
`season-<destination>-<label>`, `event-<destination>-<name>` — not from a
database row's autoincrement id. That matters because
`seed_risk_reference_data` reseeds by deleting and reinserting everything;
an autoincrement-based id would renumber on every reseed and silently break
any `risk_id` a trace or audit log had already recorded. A content-derived
id stays the same across a reseed as long as the fact itself hasn't changed.

Two checks use that id to keep the model honest:

- **`validate_grounded_response`** (`guardrails.py`) — every id in the
  model's `highlighted_risk_ids` must exist in what `propose_risks()`
  actually returned. A fabricated citation triggers a retry, then the
  no-narrative fallback.
- **`missing_escalation`** — derived from the data alone, never from the
  model's own wording: any `RiskItem` with `severity == "high"` means the
  response must have `escalate=True`, or it is rejected the same way.
  `traveler_group_risk` items are deliberately excluded from this — the
  model is never told who is travelling, so it cannot judge whether a given
  legal-status fact is personally relevant, and the prompt tells it not to
  guess. Escalation is decided by severity alone.

---

## 4. Guardrails: this agent's own, not borrowed

`guardrails.py` is a self-contained injection/bias/toxicity screen, used
before the model call (`screen_input`, on the traveller's own preferences
and refinement notes) and after it (`screen_output`, on the generated
rationale) — on both the grounded path and the prompt-only fallback, via
this agent's own `_risk_preflight`/`_risk_postprocess` in `agent.py`, never
through the generic wrapper that reaches into another agent's detectors.

Bias screening is two-tier, same reasoning as the rest of the codebase's
guardrails applied to this agent's own subject matter: a protected-attribute
mention alone (nationality, religion, gender or orientation, disability,
age) is `medium` and passes — stating that a country's law criminalises a
protected group is exactly the sourced content this agent exists to
produce. Attribute **plus** generalising language ("should not", "cannot",
"all", "never"...) is `high` and blocks — that has stopped being a fact
about the law and become a generalisation the model produced.

One injection rule is specific to this agent: `fabricate_live_fact` catches
an attempt to make the model simply assume a real-world regulatory fact
("assume the border is open") — no other specialist is asked to state
real-world regulatory facts it cannot verify, so no other agent needs it.

---

## 5. The two paths

`agent.py`'s `risk_node` branches on `provider.covers(request)`:

- **Grounded** — the destination has at least one row in
  `risk_standing_facts`. `propose_risks()` runs, `reasoning.py` narrates
  over it, every `RiskItem` becomes an `Option` on the returned
  `AgentFinding`.
- **Prompt-only fallback** — an unmapped city, or one outside the five
  seeded destinations. Falls back to the shared `INSTRUCTION` prompt with
  nothing to ground against, screened by this agent's own guardrails (not a
  borrowed generic wrapper), and `ESTIMATE_WARNING` is appended so the
  answer is never mistaken for reference-data-backed output. On this path
  there is no grounding check and no data-derived escalation — the same
  honest trade-off Flight Agent's own no-inventory fallback makes, and
  worth the same caution: today, this is the path most destinations take,
  since only five cities are seeded.

---

## 6. Leaving room to plug in real data later

Reference data is read through one interface, `RiskDataProvider` (in
`providers/`), never accessed directly from `domain.py`:

```python
class RiskDataProvider(Protocol):
    def covers(self, request: RiskProposalRequest) -> bool: ...
    def fetch(self, request: RiskProposalRequest) -> RiskFetchResult: ...
```

`DatabaseRiskProvider` is the only implementation today, reading whichever
database `flaskapp/database.py` is configured against — SQLite locally, the
same schema on Postgres/Supabase once `DATABASE_URL` is set, no code change
either way. A future live-retrieval provider (mirroring
`accessibility_agent/retrieval.py`'s bounded-search pattern) plugs in behind
the same interface with no change to `domain.py`, `reasoning.py`, or
`agent.py`. `RiskFetchResult.trust_level` already distinguishes `"authored"`
(today's tables) from `"retrieved"`, unused until that day, so the
distinction is something code can assert once it matters rather than
something to retrofit.

---

## 7. What's deliberately left for later

### 7.1 Country/region-level facts, split out from city-level ones

Several standing-fact categories today — visa/entry, currency/customs, some
health advice — are really national or bloc-level facts (Schengen's 90/180
rule, say), stored once per *city* because that is the granularity the
table uses uniformly. That is fine at five cities; it stops being fine as
coverage grows, because adding a second city in an already-covered country
means re-authoring facts that haven't changed. Splitting standing facts into
a country/region-level table and a city-level one (crime hotspots, cultural
norms, seasonal windows — things that are genuinely local) would let
coverage grow by country, which is far cheaper to author than by city, for
the categories where that's the honest granularity anyway. Not done now
because it touches the schema, the provider, and `domain.py`'s query logic
for a benefit that only matters once more than five cities exist.

### 7.2 Confidence tied to data coverage

`RiskAgentResponse.confidence` is the model's own self-reported number
today, unconstrained by how much reference data actually backed the answer
— unlike Accessibility Agent, whose confidence is capped when its own
evidence coverage is thin. Every seeded destination currently returns
10+ standing facts, so there is no real case today where a thin proposal
and a confident-sounding model response actually diverge. Worth adding a
coverage-based ceiling the day a destination (or a live provider) can
legitimately return a sparse result — not before, since there is nothing to
test it against yet.

### 7.3 Live retrieval instead of the local tables

Matches the team's own architecture notes for this agent ("visa reference
data, seasonal risk data, local events data — all explicitly synthetic and
illustrative"; live regulatory/weather feeds are out of scope). Swapping in
a real source later is possible (§6's interface exists for exactly this),
but it is a bigger change than it sounds:

1. **Visa/entry** — no single trustworthy place to search; each country
   publishes its own rules on its own site. Even "live" lookup needs a
   country-to-official-domain table, which is itself local, hand-maintained
   data — live retrieval relocates that dependency, it doesn't remove it.
2. **Seasonal/weather** — realistic to search live; national weather
   agencies are a stable, enumerable set of trustworthy sources.
3. **Local events** — the hard case. No small set of official aggregators
   exists the way it does for weather; a live search here is either wide
   open (pulls in low-quality or fake pages) or needs a long,
   constantly-stale per-city source list.

Whenever live search is added, one thing stops being optional: every
retrieved excerpt must be screened for injected instructions before a
model reads it, the same way `accessibility_agent.guardrails.
sanitize_evidence` screens its own search results — today's tables are
authored by the team, so there is nothing to sanitise yet, but a live
source is untrusted input from day one.

### 7.4 A faster, fully offline pre-check before a request reaches any agent

Not specific to this agent — a system-wide guardrail idea. Catching a
malicious request today either uses a cheap pattern-match (misses a lot) or
calls the L2 classifier (accurate, but a network call). A small classifier
running locally with no network call, sitting between the two, would catch
the obvious cases immediately and only escalate the unclear ones to L2 —
faster on average, and it keeps working if the L2 provider is unreachable.

---

## 8. Decisions the team still needs to make

1. Add `escalate`/`escalation_reason` as real fields on the shared
   `AgentFinding` (`flaskapp/travel_ai/schemas.py`) — `RiskAgentResponse`
   already carries them internally, but they are bridged into the shared
   schema's `warnings` as an `ESCALATE:`-prefixed string today, the same
   text convention Accessibility's `VETO:` uses, because changing the
   shared schema affects Accessibility and the Orchestrator too and isn't
   this agent's call to make alone.
2. Whether/when to build the live-data version (§7.3) — if so, the
   architecture notes need updating, since they currently say the opposite.
3. Whether to split country/region-level data from city-level data (§7.1)
   — a scope decision once coverage needs to grow past five cities.
4. Whether to build the offline pre-check (§7.4) — timing only.
