# Project progress

Two things in one file: a **session log** of what was done and why (Part A), and
the **Flight Agent report** covering what is built, its responsible-AI position
and its testing (Part B).

They were separate files until 2026-08-11. Merging them removed a `.gitignore`
rule that was silently swallowing any file named `progress.md`, so this log was
never actually being committed.

**How to use this file**
- Newest session at the top of Part A.
- Each entry says what changed, what was decided and why, and what is still
  open. Decisions matter more than file lists — the diff already records files.
- Claude updates Part A at the end of each coding session. If a session ends
  abruptly, the next one backfills it.
- Part B is a reference document, not a log. Update it when the thing it
  describes changes, not once per session.

**Related documents**
- [`docs/handoff/hotel_transport_agent.md`](handoff/hotel_transport_agent.md) — handoff to the Hotel & Transport developer
- [`docs/places_contract.md`](places_contract.md) — shared city/airport dataset API
- [`docs/flight_agent/inventory_sources.md`](flight_agent/inventory_sources.md) — seed vs Duffel
- [`docs/security/cicd-improvement-plan.md`](security/cicd-improvement-plan.md) — CI/CD backlog, kept separate from this log
- [`docs/individual_reports/flight_agent.md`](individual_reports/flight_agent.md) — earlier report draft, predates integration

---
---

# Part A — Session log

## 2026-08-11 — Shipped the city work; found a committed API key; CI on feature branches

**Goal:** push the city work to `origin/mark` and get CI running on it.

### What happened

| Step | Outcome |
|---|---|
| Pre-push audit | **Found a live Azure OpenAI key** in an unpushed commit; stopped before pushing |
| Secret fix | `git rm --cached .env.secrets` + amended the unpushed commit, so the key never entered the remote |
| Rebase | `origin/mark` had moved — rebased onto Andrew Tan's `7ce09bc "Delete .env.secrets"`, no conflicts |
| Push | `9c145f1` (city work) and `56cebb7` (amended CI commit) |
| CI triggers | `security.yml` now runs on push to `mark` and on `pull_request` (`39212dd`) |
| First CI run | **Green** — Secret scan ✓, Tests ✓ (261 passed), SAST ✓, DAST ✓ |
| Docs | Merged `docs/flight_agent/progress.md` into this file; removed the `progress.md` ignore rule |

### The secret finding — read this before rewriting history

`.env.secrets` was **tracked by git despite being listed in `.gitignore`**. Gitignore
only prevents *untracked* files from being added; it has no effect on a file git
already tracks. It had been carried along invisibly since 2026-08-04.

Two separate exposures, only one of which was fixable here:

1. **The unpushed commit** would have published a live key. Caught and amended
   out before pushing. Never exposed.
2. **Commit `c309d7a` (2026-08-04) is already on the remote** — on `origin/main`,
   `andrew`, `andy`, `ci-pipeline-test`, `release` and `mark`. Untracking a file
   does not remove it from history. **This is why rotating the Azure key is
   required, not optional.** Mark has since cleared the values from
   `.env.secrets`; rotation in the Azure portal is still outstanding.

Teammates hit this before: four cleanup commits on 2026-08-07 ("Delete
.env.secrets" ×2, "remove sensitive Azure credentials" ×2). The file kept coming
back because the ignore rule was never going to catch it.

### Mistake made and corrected

The rebase checkout **overwrote the local `.env.secrets` with an empty template**,
wiping the working key from disk. A backup taken before any git operation was
restored, verified by checksum. Lesson for next time: back up ignored-but-present
config files before any rebase or branch switch, because they are invisible to
`git status` and so are invisible in a "what will this change?" check.

### The same trap, twice, in opposite directions

Worth naming as a pattern, because it cost time twice in one session:

| File | Symptom | Cause |
|---|---|---|
| `.env.secrets` | ignored but **tracked** — nearly published a key | ignore rule added *after* the file was committed |
| `docs/progress.md` | untracked and **silently skipped** by `git add -A` | bare `progress.md` pattern matching at any depth |

`.gitignore` only governs untracked files, and a bare filename pattern matches
in every directory. `git add -A` skips ignored files without a word, so a file
can appear "committed" when it was never staged. `git check-ignore -v <path>`
answers both questions in one command.

### CI decisions

1. **Feature branches are listed explicitly, not by wildcard.** Shared repo —
   enabling CI on every branch spends Actions minutes on other people's
   throwaway branches. Add a branch name to the two lists in `security.yml`.
2. **Added a `concurrency` group** so a second push cancels the superseded run.
   DAST alone is ~1m37s.
3. **The two red ✗ annotations on a green run are by design.** Both are
   `continue-on-error` steps: the gitleaks *history* scan (fails on `c309d7a`,
   will until history is rewritten) and Semgrep's `auto` ruleset (8 pre-existing
   findings, marked TEMP pending team review). The blocking checks — gitleaks
   *working tree*, pytest, and the 7 hand-written LLM/agent rules — all passed
   clean, the last with 0 findings across 49 files.

### Still open

- **Rotate the Azure OpenAI key.** The old one is in public history via
  `c309d7a`. This is the top item.
- **PR checks run but do not block.** `pull_request` triggers the workflow, but
  making checks *required* is a GitHub branch-protection setting, not something
  a workflow file can express. Needs repo admin:
  `Settings → Branches → Require status checks to pass before merging`.
- **Decide on history rewrite.** `git filter-repo`/BFG across all branches would
  clear the gitleaks history scan, but needs a coordinated force-push. Rotation
  may make it unnecessary.
- **`instance/travel_planner.sqlite3` is still tracked** and the test suite
  writes to it. Same trap as above: it is listed in `.gitignore` (`instance/*.sqlite3`)
  but was committed first, so the rule does nothing. Needs `git rm --cached`.
- Carried over: the Duffel multi-airport fan-out has still never run against the
  real API.

---

## 2026-08-10 / 08-11 — City-level origin and destination

**Goal:** stop planning trips by country alone, so a traveller can pick Osaka
rather than being silently routed to Tokyo.

### What was built

| Area | Change |
|---|---|
| Shared dataset | New `flaskapp/places.py` — 254 cities, 61 countries, 284 airports, keyed on stable slugs (`jp-tokyo`) |
| Contracts | `TravelRequest` gained optional `origin_city` / `destination_city`; `TripContext` gained `origin_city`, `origin_airports`, `dest_airports` |
| Resolution | `airports.py` rewritten over `places.py`; returns *every* airport serving a city, plus written reasons when it can't resolve |
| Route matching | `domain._matches_route` changed from `==` to `in` — the core of the feature |
| Live search | Duffel provider fans out one search per airport pair, capped at 2 airports per city, merged and de-duplicated |
| Intake form | Country → city dependent `<select>` pair; cities embedded as a JSON data block (18.5 KB), no extra request |
| Persistence | `travel_requests.origin_city` / `destination_city`, nullable, via the existing `ALTER TABLE` migration pattern |
| Seed data | 104 → 284 rows, 19 airports; the original 100 are byte-identical |

### Decisions and why

1. **Country stays the field of record; city is additive.** Replacing
   `origin`/`destination` would have rippled into the `NOT NULL` DB columns, the
   admin dashboard's `json_extract` queries, and Risk & Advisory's
   country-level reasoning — for no gain. Old requests stay valid untouched.

2. **The dataset lives at `flaskapp/` level, not inside `flight_agent/`.**
   Hotel & Transport keys its inventory on the same cities. One dataset, one
   place, so the two cannot drift.

3. **Cities are keyed on a stable slug, not the display name.** Display names
   get corrected; anything holding a foreign key to a display string breaks when
   that happens. Downstream databases join on `slug`.

4. **A city resolves to every airport serving it, not one primary.** This is the
   whole point of collecting a city. Verified payoff: a Tokyo search now returns
   `NH612 -> HND at SGD 482` alongside the Narita options — that flight was
   invisible before.

5. **Country-only requests still work, but say so.** They fall back to the
   country's main city and the assumption appears in the finding
   ("assumed Tokyo (NRT/HND)") rather than being applied silently.

6. **Duffel is capped at 2 airports per city.** Each pair is a separate billed,
   rate-limited call; London → Tokyo unbounded would be 8 searches per plan.
   When the cap bites, the traveller is told.

7. **Seed coverage is derived from the rows, not a hand-maintained list.** The
   old `SEED_BACKED_COUNTRIES` had to be edited in lockstep with the CSV and
   went stale silently. `covers_route()` now asks the data.

8. **Hong Kong is a city of China** (`cn-hong-kong`) — team decision, 08-11.
   `countries.py` has no separate entry, so this is what makes its existing HKG
   inventory reachable. Confirmed working: `Singapore → Hong Kong` returns
   CX105 at SGD 198 and SQ237 at SGD 545, flights that were previously dead data.

### Tests

261 passing. New: `tests/test_places.py` (dataset integrity),
`tests/test_flight_multi_airport.py` (the two-airport behaviour), plus city
coverage in `test_flight_adapter.py`, `test_database.py`, `test_api.py`.

The golden-scenario and bias-audit tests passed **unmodified** — a `TripContext`
validator reconciles the old scalar airport fields with the new lists in both
directions, so every pre-existing caller kept working.

### Still open

- **The Duffel multi-airport fan-out has never run against the real API.** It is
  proven with a stubbed HTTP session only. The underlying mapping code is
  unchanged and was smoke-tested before, but the fan-out is new.
- **`hotel_transport_agent/prompt.py` was edited by me**, though its header says
  the Hotel developer owns it. Two sentences about city anchoring. Flagged in
  the handoff as a placeholder to reword.
- Flights exist only on specific dates between 2026-08-24 and 2026-10-08.

---

## Earlier work (reconstructed from git history)

Recorded for continuity; these predate this log, so the detail is thinner.

### 2026-08-07 — Security CI
CI gates on tests, secret scanning and LLM-specific rules; release covered.
Supporting notes in `docs/security/`.

### 2026-08-05 — Flight Agent build-out
The bulk of Flight Agent landed in one day: the deterministic flight layer
(`domain.py`), contracts reconciled with the intake form, the graph node running
on real inventory, a fix guaranteeing unverified options are disclosed in the
plan, plus the scenario input/result log and progress report. **Part B below is
the detailed report from this period.**

### 2026-08-01 → 08-04 — Persistence and templates
SQLite persistence for users, travel plans and audit data. Admin and chat
templates reworked; feedback mechanism added.

### 2026-07-25 → 07-27 — Protocol and security scaffolding
A2A communication protocol; agent file structure; SAST/DAST workflow added and
then tuned (false positives fixed, made non-blocking on main pending review).

### 2026-07-11 → 07-18 — Project setup
Initial Flask template and the first agentic travel-planning scaffolding.

---
---

# Part B — Flight Agent report

**Owner:** Mark
**Period covered:** to 2026-08-05, with counts refreshed 2026-08-11
**Branch:** `mark`
**Status:** integrated into the LangGraph workflow and verified against a live model

---

## 1. Summary

The Flight Agent now produces flight options drawn from real inventory rather than
generated by a language model. Search, filtering and ranking run as ordinary Python;
the model is used only to explain a result the code has already determined, and a
grounding check rejects any flight it names that does not exist in that result.

This closes the gap that mattered most: before this work, every flight number, fare
and seat count in a travel plan was invented. The agent's prompt forbade that, but
nothing enforced it.

Three properties now hold, each covered by tests:

| Property | Behaviour |
|---|---|
| Model unavailable | Options are unchanged; only the written rationale is lost |
| Model names a non-existent flight | Retried, then discarded — it never reaches the traveller |
| Route outside loaded inventory | Falls back to model estimates, explicitly labelled as such |

---

## 2. What was built

### 2.1 Deterministic layer

| Module | Responsibility |
|---|---|
| `domain.py` | Search, filter and rank inventory; record a reason for every rejected flight |
| `schemas.py` | Typed contracts (`extra="forbid"`, so an invented field is rejected on arrival) |
| `guardrails.py` | Input screening (injection, bias, toxicity) and output grounding |
| `reasoning.py` | The model layer over the tool's output, with retry-then-fallback |
| `seed_data.py` | 284 inventory rows across 19 airports (104 across 5 routes at the time of writing) |
| `prompt.py` | Both prompts — the grounded one and the fallback |

Ranking is lexicographic rather than a weighted score, so each position is individually
nameable ("violates avoid_red_eye", "later than the traveller's soft arrival
preference"). This keeps the ordering explainable instead of an opaque number.

`screen_flights()` returns a record for *every* same-route inventory row, included or
not, with the specific reason. Rejections are as much a part of explainability as
survivors: "why not that flight" is a question a traveller can reasonably ask.

### 2.2 Integration with the shared system

| Module | Responsibility |
|---|---|
| `agent.py` | The LangGraph node — deterministic when inventory covers the trip, fallback otherwise |
| `adapter.py` | Translation between the team's `TravelRequest` and the flight contracts |
| `airports.py` | Country + city → airport resolution (data lives in `flaskapp/places.py`) |

`adapter.py` is the only module that knows both schemas. As other agents evolve the
shared contract, the changes land in one file rather than throughout the domain logic.

### 2.3 Defects found and fixed

**Accessibility filter was inert.** `domain.py` tested `"wheelchair" in accessibility_needs`
— exact list membership. The intake form sends free text prefixed per traveller
(`"Traveler 1: wheelchair assistance"`), so the condition never matched and the filter
did nothing for every real request. It now strips the prefix and matches on substring.
This was the single highest-impact fix in the period: the control existed, passed its
tests, and protected nobody, because its tests used the idealised input rather than the
form's actual output.

**Audit chain had no verifier.** `AuditTracer` hash-chains every event, but
`verify_hash_chain()` was absent from this repository — traces were tamper-evident in
principle with no means of detecting tampering. Restored with tests.

**Refinement notes bypassed screening.** Free text from the "Refine your plan" panel
reached the model without passing the injection, bias and toxicity gates that the
initial preferences go through. Now screened identically.

**Provenance flags did not survive synthesis.** Found during manual testing — see §5.3.

---

## 3. Responsible-AI position

### 3.1 Bias

Ranking uses schedule, price, stops, seat availability and accessibility. Nothing else.

`traveller_genders` is collected by the intake form and is deliberately carried into
the model's context, against the usual instinct to strip a protected attribute at the
boundary. The reason is that an XRAI bias audit cannot examine a field the agent never
receives: holding everything else constant and varying only gender is what reveals
whether the model's rationale shifts on an attribute it was given no reason to use.

The risk is accepted knowingly — a field in the model's context is a field the model
can condition on. That is the finding the audit is looking for, not a side effect to be
designed away. What makes such a finding *attributable* is the control:
`test_flight_bias_audit.py` asserts that deterministic ranking and screening reasons are
identical across all four gender values, plus a structural check that the string
"gender" appears nowhere in `domain.py`. Any difference an audit observes therefore
originates in the model, not the code.

Verified live (§5.2): identical flights offered for `female` and `male` on an otherwise
identical request.

### 3.2 The disability tax

Flight Agent contributes to this system-level effect deliberately and visibly. When
accessibility needs include a wheelchair, flights without assistance are excluded before
any other agent sees them — this shrinks the candidate set early. That is a design
choice, not a hidden one: it appears in `screen_flights()` output as an explicit reason,
so the effect is measurable rather than merely suspected.

On seat pricing: accessible seats are always free, while extra-legroom and exit-row
seats carry fees. Charging for an accessible seat even at parity with extra-legroom
would not be fair, because the disability tax is about a required accommodation being
mandatory rather than optional. A non-disabled traveller can always take the free
standard seat; a wheelchair user for whom the accessible seat is the only usable one
cannot. Same fee, different meaning.

### 3.3 Security

| Risk | Gate | Status |
|---|---|---|
| Prompt injection via free-text preferences | Pre-model | Blocked before any model call |
| Biased or stereotyping input | Pre-model | Two-tier: attribute alone is medium and passes; attribute plus stereotype is high and blocks |
| Toxic input | Pre-model | Keyword screening |
| Biased or toxic model output | Post-model | Same detectors applied to the rationale |
| Hallucinated flight ID | Post-model | Every cited ID must exist in the actual proposal |
| Trace tampering | Post-response | `verify_hash_chain()` |
| Rate limiting | — | **Not built.** Relies on whatever the API gateway provides |
| Inventory poisoning | — | **Not applicable yet.** Seed data is static and version-controlled |

---

## 4. Automated testing

261 tests, all passing (168 at the time this report was written; the level breakdown
below is from that count and has not been re-apportioned). They span three levels —
describing them as "mainly unit tests" would be inaccurate.

| Level | ~Count | Examples |
|---|---|---|
| Unit | ~70 | `test_flight_domain`, `test_flight_seats`, `test_flight_guardrails`, `test_flight_preferences`, `test_places` |
| Integration | ~93 | `test_flight_node` (node → adapter → domain → reasoning → guardrails → A2A → tracer), `test_flight_agent`, `test_flight_adapter`, `test_safeguards`, `test_api` (real Flask client and SQLite) |
| Functional / acceptance | 5 | `test_flight_golden` — golden scenarios asserted against specified expected outcomes |

**Every one of them stubs the language model.** That is deliberate — it makes them fast,
free and deterministic, so they can run in CI. It also means they verify the code
*around* the model and never the model's own output.

**Stated limitation:** no automated test exercises a live model, so rationale quality and
relaxation judgement are verified only by hand, and only when someone remembers to run
it. This is a real gap, not an oversight, and the manual testing in §5 is what currently
compensates for it.

Reproducing the logs:

```powershell
python -m pytest -v > docs/test_reports/pytest-verbose.txt
python scripts/report_flight_scenarios.py > docs/test_reports/flight-scenarios.txt
```

The second is deterministic — no model, no network — and prints each scenario's input
alongside its actual result, which `pytest -v` does not show.

---

## 5. Manual acceptance testing (UAT)

Two scenarios executed by hand through the browser against a live model, a real
database and the full five-agent workflow. They were chosen to exercise the two paths
that behave differently: a route the inventory covers, and one it does not.

### 5.1 Scenario A — covered route

**Input**

| Field | Value |
|---|---|
| Route | Singapore → Japan |
| Dates | 2026-09-01 to 2026-09-05 |
| Travellers | 1 (age 34) |
| Budget | SGD 4,000 |
| Accessibility | wheelchair assistance |
| Preferences | direct flights |

**Expected:** flight options limited to real inventory rows, with concrete fares and no
"estimates" label.

**Actual** (final plan, abridged):

```
2026-09-01 – SQ632 dep SIN 08:00 (+08) → arr NRT 15:10 (+09)
2026-09-05 – SQ633 dep NRT 11:00 (+09) → arr SIN 17:00 (+08)
Alternatives: SQ636 (arr 23:40) + SQ637 (dep 23:00)
Estimated total: SGD 2,840
  flights ≈ SGD 1,040 (flight_agent) + hotel estimate ≈ SGD 1,800
Assumption carried into the plan: "Flight times/fares and seat availability are
from the project's static inventory and must be verified with the airline."
```

**Analysis — PASS.**

Every flight fact matches the seed inventory exactly: four flight IDs, both timestamps
with correct per-airport UTC offsets (`+08` at Singapore, `+09` at Narita), and fares of
520 + 520 = the stated 1,040. The model computed none of this.

The `INVENTORY_ASSUMPTION` string attached in `agent.py` to every option survived
orchestrator synthesis **verbatim** into the final plan — provenance propagated
end-to-end.

Two further observations:

*The contrast is visible in one screen.* The hotel in the same plan is
"Hilton Tokyo (Shinjuku) is a placeholder chain property" at an *estimated* SGD 1,800.
Flights are named, priced and sourced. Two different epistemic standards, side by side —
this agent versus the placeholder agents.

*An unplanned validation.* Flight Agent ranked `SQ636` first (cheapest at SGD 380). The
orchestrator selected the more expensive `SQ632` (SGD 520) instead, reasoning that a
23:40 arrival complicates assisted transfers. That is precisely the renegotiation logic
encoded in golden scenario 1 — and the orchestrator arrived at it independently, from
the arrival times this agent supplied. Deterministic ranking proposes; accessibility
reasoning disposes.

*Risk noted (since partly addressed).* The plan's alternatives included routings via
Haneda (HND) and Kansai (KIX), neither of which had inventory at the time. They were
correctly labelled unverified, but the asymmetry was worth recording: this agent is
constrained to real data while the placeholders are not, so a traveller selecting an
alternative silently leaves grounded territory. As of 2026-08-11 both HND and KIX carry
seed inventory, so this specific example no longer applies — the general asymmetry does.

### 5.2 Scenario B — uncovered route

**Input:** identical to Scenario A except destination = **Brazil**.

**Expected:** no invented flight numbers; options clearly labelled as estimates.

**Actual** (final plan, abridged):

```
Proposes a one-stop routing on Qatar Airways via Doha to São Paulo (GRU).
"exact times unknown; verify with airline"
"fares for Sep 2026 are unknown and may exceed budget"
No flight numbers given. No flight total given (local spend ≈ SGD 430 only).
Alternatives: Emirates via DXB, Turkish via IST, Air France via CDG.
```

Flight Agent's own finding carried both warnings:

```
No airport mapping for destination country 'Brazil'
These flight options are model estimates, not drawn from verified inventory.
```

**Analysis — PASS, with one defect found.**

What is *absent* is the result. No flight numbers, no times, no fares — against
Scenario A's four IDs and exact fares. The model named four real airlines and their hub
structures, which is reasonable, but **invented no flight numbers at all**, which is the
shared system policy holding under pressure: it had no data and still declined to
fabricate the detail that would look most authoritative.

The model also caught something the deterministic tool structurally could not:
*"Return_date (5 Sep) is interpreted as departing Brazil that day; arrival back in
Singapore likely 6–7 Sep... If you need to be back in Singapore by 5 Sep, the trip is
infeasible on standard routings."* With no Brazil inventory there is nothing to compute
over, and that is real reasoning about a request that does not fit.

*Note (2026-08-11):* Brazil now resolves to São Paulo (GRU/CGH) via `places.py`, so this
scenario's first warning has changed from "no airport mapping" to "no flight inventory
loaded" — which matches the scenario's own label better than the original did. The
fallback behaviour under test is unchanged.

### 5.3 Defect found by manual testing

Neither of Flight Agent's warnings appeared in the orchestrator's final plan. They were
paraphrased into "No live fares, schedules, or seat availability have been checked",
placed near the bottom of a long Limitations section.

Semantically close, and reached independently — but note the asymmetry with Scenario A,
where the provenance note propagated word for word. **Provenance labels survive synthesis
when attached to an Option, but not reliably when placed in `warnings`.** A reader
skimming Scenario B's plan encounters confident detail — "2.5–4h layovers", "~25–30h
travel time" — and must reach the bottom to learn that none of it is verified.

**Fix applied.** `safeguards.enforce_provenance_disclosure()` now writes the statement
into `plan.limitations` as the first entry whenever any specialist flags its options as
estimates, and `assess_plan` fails the assessment. Checking whether the model *happened*
to paraphrase adequately would be a test of luck; writing it in makes disclosure a
property of the system. Seven tests cover it.

This is the clearest argument in this report for manual testing alongside the automated
suite: the passing tests did not surface this, because every one of them examines a
single agent's output rather than what the orchestrator does with it downstream.

---

## 6. Known limitations

1. **Inventory is static.** 284 rows, SIN-origin hub-and-spoke across 19 airports,
   dates 2026-08-24 to 2026-10-08. Everything else takes the fallback path.
2. ~~**One airport per country.**~~ **Resolved 2026-08-10.** Intake now collects
   country + city (`flaskapp/places.py`, 254 cities / 284 airports), and a city
   resolves to *every* airport serving it, so a Tokyo trip ranks Haneda and Narita
   together. Country-only API requests still work by falling back to the country's
   main gateway, and that assumption is disclosed in the finding rather than applied
   silently. See [docs/places_contract.md](places_contract.md).
3. **No live model in CI.** See §4.
4. **Relaxation context-sensitivity is unproven.** `demo_multi_gap_relaxation.py` runs a
   family and a solo traveller through an identical dual-gap scenario to test whether the
   model's choice of which preference to relax tracks who is travelling. On the live run
   both chose `soft_arrival_preference`. That does not show the model ignores context —
   both may be equally valid here — but the demonstration is currently inconclusive and
   should not be cited as evidence. A scenario where the two contexts clearly ought to
   diverge would settle it.
5. **No rate limiting** of its own.
6. **Renegotiation is not wired.** `FlightConstraints` and `negotiation_history` are
   implemented and tested, but no orchestrator loop issues them yet, so the
   renegotiation path is exercised only by the golden scenarios.

---

## 7. Next

1. Sharpen the multi-gap fixture so limitation 4 can be resolved either way.
2. ~~Collect a destination city or airport at intake~~ — done 2026-08-10.
3. Wire the renegotiation loop in `graph.py` so `FlightConstraints` is used in anger.
4. Add a small live-model smoke test to CI, gated on a key being present.
5. Extend inventory coverage, or connect a real supplier API behind `domain.py`'s
   existing interface — the Duffel provider now does this, but its multi-airport
   fan-out has not been exercised against the live API.

---

## 8. Reference

| Item | Path |
|---|---|
| Agent code | `flaskapp/travel_ai/agents/flight_agent/` |
| Shared city dataset | `flaskapp/places.py` |
| Tests | `tests/test_flight_*.py`, `tests/test_places.py`, `tests/test_tracing.py`, `tests/test_safeguards.py` |
| Golden fixtures | `tests/golden/flight_scenarios.json` |
| Test logs | `docs/test_reports/` |
| Live demos | `scripts/demo_golden_scenario.py`, `scripts/demo_multi_gap_relaxation.py` |
| Scenario log generator | `scripts/report_flight_scenarios.py` |
| End-to-end harness | `scripts/e2e_test.py` |
| Duffel smoke test | `scripts/duffel_smoke.py` |
| Earlier report draft | `docs/individual_reports/flight_agent.md` (predates integration) |
