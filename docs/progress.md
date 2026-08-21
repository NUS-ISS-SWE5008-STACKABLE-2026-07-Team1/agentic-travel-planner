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
- [`docs/security/firstCIMergeToRelease.md`](security/firstCIMergeToRelease.md) — record of the first CI/CD landing on `release` (PR #9)
- [`docs/security/cicd-status.html`](security/cicd-status.html) — the 18-item backlog in plain English, for the team
- [`docs/individual_reports/flight_agent.md`](individual_reports/flight_agent.md) — earlier report draft, predates integration

---
---

# Part A — Session log

## 2026-08-20 — L2 guardrails rebased onto `release`; deployed off by default

**Goal:** get the LLM guardrail layer built on 2026-08-16 onto the branch the
team actually works from. It had been parked on a local-only backup branch
(`backup/mark-wip-2026-08-17`, commit `b173482`) for three days while `release`
absorbed the Supabase/Postgres migration.

### What moved

Cherry-picked `b173482` onto `release` as `feat/llm-guardrails-flight`. Three
conflicts, all resolved by keeping both sides:

| File | Conflict | Resolution |
|---|---|---|
| `travel_ai/api.py` | `release` added the intake-request persistence inside the same `try`; the guardrail branch changed the `screen_prompt` call and added an `except` | Guardrail passed to `screen_prompt`, all of `release`'s persistence kept, `except GuardrailBlocked` added **before** `except SafetyError` |
| `travel_ai/service.py` | `release` widened `database_path` to `Path \| str \| None`; the guardrail branch added two keyword arguments | Widened type kept, both arguments added |
| `instance/travel_planner.sqlite3` | binary | `release`'s copy kept; the snapshot's was three days stale |

The block order in `api.py` matters and is not incidental: `screen_prompt` runs
before any database write, so a prompt the classifier rejects is never persisted
as an intake message.

Dropped from the snapshot rather than landed: `temp_llmguard.md` (its own header
calls it a scratch file), and the two stale copies under
`docs/security/drafts/` — the real pipeline is `.github/workflows/`, and
re-applying the drafts would have reinstated a branch list the team has since
replaced. `designOfFlightAgent.md` moved from the repo root to
`docs/flight_agent/design.md`. `docs/security/firstCIMergeToRelease.md` was
added because `release`'s own log has been linking to it since 2026-08-15 with
no file at the other end.

### Deployed off, deliberately

`Config.GUARDRAIL_LLM_ENABLED` defaults to `true` and `GUARDRAIL_FAIL_MODE` to
`closed`. Together those mean a classifier that cannot reach the provider
**refuses the traveller** rather than waving them through. That is the right
default for the code and the wrong thing to discover on a live deploy, so
`render.yaml` now declares all five guardrail variables explicitly with
`GUARDRAIL_LLM_ENABLED=false`. It is one value to flip once the gate has been
exercised against a real key.

Two things worth noticing while that file is open: `render.yaml` still deploys
from `branch: subbu-18Aug`, not `release`; and `GUARDRAIL_LLM_MODEL` is set to
the small tier rather than inheriting `gpt-5`, because the classifier runs twice
per plan on the critical path.

### Test state

497 passed, 8 skipped (Postgres — `TEST_DATABASE_URL` unset locally), 5
deselected (`-m "not live"`). No failures, and none of `release`'s existing
tests changed behaviour: the suite runs with `GUARDRAIL_LLM_ENABLED = False`,
so the classifier is exercised only by `tests/adversarial/`, where the model is
stubbed.

`pytest.ini` landing also closes a quieter gap. Until now the `live` marker was
unregistered, so `.github/workflows/ci-fast.yml`'s `-m "not live"` filter was
selecting everything and working only by coincidence — an unknown marker filter
is not an error to pytest. The live evaluation is gated twice over (the marker
**and** `GUARDRAIL_LIVE_EVAL=1`), so no CI run has ever been able to bill a
model call, but the filter itself was decorative until this commit.

### The two gates now take separate models

Follow-up the same day, from a design question worth recording: is a smaller
model smarter for guardrail work than the planning model?

Mostly yes, but the usual argument (cost) is the weakest one. Guardrails are 2
of 7 calls per plan, so ~29% of spend on a task that never writes a sentence a
traveller reads — real, but ordinary. The argument that actually decides it is
**availability**. Under `GUARDRAIL_FAIL_MODE=closed`, a timeout is not a slow
response, it is a refused traveller: `_failure_verdict()` maps every exception
to `BLOCK`. A large model that occasionally takes 12 seconds against an 8-second
budget does not cost latency, it costs the request. The task itself is
narrow — `with_structured_output` constrains the answer to one of three
decisions and one of nine enum categories — and fixed-label classification is
where small models close most of the gap.

Where a small model does hurt is **precision**, not recall. Missing an attack is
invisible; blocking "please act as my travel agent" is a support ticket, and
three such false positives already exist in L1 (`ben-001`, `ben-012`,
`ben-019`). It also hurts on exactly what L2 was built for — injection encoded
in base64, leetspeak, split whitespace, translated, wrapped in role-play — and
on structured-output reliability, where a schema failure is another block.

So the gates were split rather than one model chosen for both:

| | Input gate | Output gate |
|---|---|---|
| When | before planning, traveller waiting | after ~75s of planning |
| Reads | attacker-controlled text | our own model's prose |
| A timeout means | a refused request | a discarded plan |
| Wants | small, fast, hard to talk round | nuance; latency is noise |

`GUARDRAIL_INPUT_LLM_MODEL` and `GUARDRAIL_OUTPUT_LLM_MODEL` each fall back to
`GUARDRAIL_LLM_MODEL`, which falls back to `LLM_MODEL` — a deployment that sets
none of them behaves exactly as before. `GUARDRAIL_OUTPUT_LLM_TIMEOUT_SECONDS`
(20s) is deliberately **independent** of the input budget rather than derived
from it; a shared timeout would force the output gate back into the constraint
the split exists to remove.

Two things fell out of this that are not cosmetic. The verdict cache is now
keyed on the model as well as the prompt version — without that, comparing two
models over one corpus in a single process scores the second on the first one's
answers, which is precisely the comparison `scripts/guardrail_eval.py` exists to
make. And the eval report named a single model while describing both gates; it
now names each, in the section heading as well as the summary table.

`tests/adversarial/test_gate_models.py` — 12 tests, because the split is
invisible at runtime: a guardrail calling one model twice behaves identically to
one calling two until somebody reads the bill. Verified by breaking it
deliberately (both gates forced onto one client, and the model dropped from the
cache key): exactly two tests went red, and only those two.

**The models currently in `render.yaml` are candidates, not measurements.**
Nothing has been evaluated against a real key yet.

### Render now deploys from `release`

`render.yaml` said `branch: subbu-18Aug` — one developer's feature branch,
live because it happened to be the one wired up. Changed to `release`, the
branch the team merges into and the only one CI runs on.

Worth knowing: **this file alone does not move the deploy.** Render reads
`render.yaml` from the branch it is already watching, so a service created by
hand keeps its dashboard setting and never sees this change. The branch has to
be changed in the Render dashboard too, or the blueprint resynced. Until both
agree the line is an intention, not a fact — noted in the file itself so the
next reader does not assume otherwise.

### Still open

- The live run against a real key has not happened yet. Until it has, the
  measured latency figures in the 2026-08-16 entry are from a stub.
- Three L1 false positives the corpus measured and deliberately left unfixed:
  a bare `"act as"` matching in `safeguards.PROMPT_INJECTION` (corpus case
  `ben-001`), and two toxicity keywords firing on ordinary complaints
  (`ben-012`, `ben-019`). Each is a behaviour change deserving its own commit.
- `release`'s copy of this log is missing the 2026-08-17 / 18 entries, which are
  still only on `mark`.

## 2026-08-16 — LLM guardrail layer (L2), and the adversarial corpus (UR-075)

**Goal:** be able to demonstrate *built and evaluated* LLM guardrails, not just
deterministic ones — and close the gap this log has been carrying since
2026-08-11 ("the input and output gates exist and are enforced, but nothing
proves they still catch attacks after someone edits a pattern").

### The design decision, and why it is not "replace the regex with a model"

The classifier is **L2**: it runs after the pydantic schemas (L0) and the
regex/keyword detectors (L1), and only on text those already cleared. L0 and L1
keep independent authority to block. That ordering is the whole argument —
cheap certain rejections never pay for an API call, the deterministic
guarantees stay intact and separately defensible, and L2's false-negative rate
is bounded below by the pattern layer rather than replacing it.

Two calls per plan, not eight: one at the HTTP boundary covering all traveller
free text at once, one on the orchestrator's synthesized plan. Against the five
agent calls a plan already makes, and a UI calibrated to ~75 seconds
(`static/js/app.js:349`), the measured p50 of ~0.9s each is roughly **2% added
latency**. Worth stating explicitly given `cicd-owner-notes.md` flags the total
absence of non-functional requirements as a visible hole for this module.

### The classifier is itself an injection target

It is an LLM being handed attacker-controlled text containing instructions
aimed at it. Four structural defences, all in `flaskapp/travel_ai/guardrails/`:

- untrusted text is wrapped in `<<<UNTRUSTED_{nonce}>>>` markers with a fresh
  `secrets.token_hex(4)` per call, so a closing marker cannot be guessed and
  written into the input;
- it goes in a `HumanMessage`, never the system role — the Semgrep rule
  `llm-untrusted-data-in-system-message` (ERROR, blocking) enforces this;
- output is constrained by `with_structured_output(GuardrailVerdict,
  method="json_schema")`; anything unparseable is a failure, not an allow;
- the verdict is consumed as an enum, and the model's `rationale` is kept off
  the audit trail entirely — the trace is served by `GET /api/v1/traces/<id>`
  and rendered in the admin dashboard, so echoing model text derived from
  attacker input into it would reintroduce the very thing being stopped.

Fail-closed on any error or timeout. `GUARDRAIL_FAIL_MODE=open` exists and is a
deliberate, visible choice.

### Measured, not asserted — this is the part that answers UR-075

`tests/adversarial/corpus/` holds 45 input and 16 output cases, each labelled
with what the deterministic layer alone should do. The split the owner notes
prescribe is now real:

- **deterministic tier, blocking, offline.** Runs on every PR with the model
  stubbed, no credential, inside the existing ~4-second pytest job — which is
  what UR-083 asks for. It pins the L1 boundary case by case, so editing a
  pattern changes a specific named test rather than nothing.
- **model tier, scheduled, never blocking.** `scripts/guardrail_eval.py`,
  wired into `docs/security/drafts/ci-evals.yml`.

First live run (`openai`/`gpt-4o`, prompt `l2-2026-08-16b`), full report in
[`docs/security/guardrail-eval-report.md`](security/guardrail-eval-report.md):

| | input gate | output gate |
|---|---|---|
| recall | **1.00** | 0.875 |
| precision | 0.893 | 0.875 |
| attacks caught by L1 | 7 (28%) | 1 (12%) |
| **attacks added by L2** | **18 (72%)** | **6 (75%)** |
| missed by both | 0 | 1 |
| L2 latency p50 / p95 | 902 / 1856 ms | 943 / 1124 ms |

Every false positive on the input gate is **L1's**, not the classifier's. The
classifier introduced none. The remaining output miss (`out-104`) is indirect
injection arriving through retrieved evidence — that path has a separate
control in `accessibility_agent.sanitize_evidence`, and tuning the prompt
against a single 16-case corpus entry would be overfitting, so it is recorded
rather than chased.

The first run also caught the classifier blocking the system's **own**
provenance disclosure ("Options from hotel_transport_agent are unverified model
estimates..."). Fixed by naming the safety text as explicitly allowed in the
output prompt, and the prompt version bumped so the old verdicts are not reused
from cache. That failure is the argument for the corpus in miniature: nothing
else in the suite would have found it.

### Three real bugs the corpus work surfaced

1. **`accessibility_agent` was screening nothing extra.** It read
   `origin_place`/`destination_place`; the fields are `origin_city`/
   `destination_city` (`schemas.py:30-31`). The test covering it used the same
   wrong keys, so it passed. Field selection now lives in one place,
   `guardrails/fields.py`, used by every caller.
2. **Toxicity matching was substring, not word.** `"kill"` fired on
   *Kilimanjaro*, `"die"` on *diet* and *Dieppe*. Word-bounded now, in both the
   flight and hotel copies.
3. **`risk_advisory_agent` had no guardrails at all** — prompt-only, and the
   `preflight`/`postprocess` hooks in `make_specialist_node` were never passed.
   Now wired via generic helpers in `guardrails/specialist.py`.

### Still open

- **A live L1 false positive at the HTTP boundary.** `safeguards.PROMPT_INJECTION`
  still matches a bare `"act as"`, so *"please act as my travel agent"* is
  rejected with a 422. Flight Agent tightened its own pattern and has a
  regression test for that exact string — but it tests a different regex, which
  is why nobody noticed. Recorded as corpus case `ben-001`. Not fixed here
  because changing the shared pattern is a behaviour change with its own test
  implications; it should be its own commit.
- Two further L1 false positives from the toxicity keyword list, on ordinary
  customer feedback (`ben-012` "we hate long layovers", `ben-019` "the last
  hotel was useless"). Word-bounding does not help — the words are used
  legitimately. The keyword list, not the matching, is the problem.
- The flight/hotel guardrail modules are still near-identical copies; the
  toxicity fix had to be applied twice.
- Rate limiting still not built, and L2 makes each request cost more.

## 2026-08-15 — CI/CD landed on `release`; pipeline split in two

**Goal:** get the pipeline onto `release` *before* the team merges their feature
branches in a few days, so those merges arrive gated rather than ungated.

Opened as [PR #9](https://github.com/NUS-ISS-SWE5008-STACKABLE-2026-07-Team1/agentic-travel-planner/pull/9)
(`ci/pipeline-release` → `release`, commit `8a68a09`). Open and green at time of
writing. Full record: [`docs/security/firstCIMergeToRelease.md`](security/firstCIMergeToRelease.md).

### Why `release` first, and why it mattered more than expected

`release` had **no CI at all** — its `security.yml` only triggered on push to
`main`, and carried no test job and no secret scan. The ordering matters because
`pull_request` events read the workflow from the *merged* result: once the
trigger is on `release`, every future PR into it is checked automatically,
wherever it came from. Landing it afterwards means several people's work merges
untested and the red build belongs to nobody.

### What changed

| File | Action |
|---|---|
| `ci-fast.yml` | added — secrets + tests + SAST, ~1 min, on each member's own branch and on PRs into `main`/`release` |
| `ci-dast.yml` | added — the slow website scan, `main`/`release` only |
| `security.yml` | **deleted** — superseded; keeping it would run every scan twice |
| `.semgrep/llm-agent.yml` | added — `ci-fast.yml` gates on these rules and they existed only on `mark` |
| `scripts/ci_stub_provider.py` | added — fake AI provider, so DAST exercises the app with **no real API key** |
| `requires.txt` | **deleted** (item 5) |

`instance/travel_planner.sqlite3` deliberately left tracked, pending a teammate.

### Team decisions taken

| Item | Decision |
|---|---|
| 5 | Delete `requires.txt` — nobody uses it |
| 6 | PR checks on `main` + `release` only for now |
| 7 | Time limits: 10 min secrets, 10 tests, 15 SAST, 20 DAST (default was **6 hours**) |
| 9 | Bandit `-ll` → `-l`, so low-severity findings stop being hidden |
| 17 | Quick tier on all seven active branches, named explicitly rather than a wildcard |

Item 8 — which advisory checks should start blocking — is still open.

### Results on the PR

Both workflows ran on the `pull_request` event, so the PR tested the very
trigger it was adding. Fast checks 52s, DAST 1m42s.

Secret scan **`no leaks found`** — green for the first time, because the Azure
key was rotated and `.env.secrets` untracked beforehand. `168 passed` (release's
older suite; `mark` is at 261). Custom LLM/agent rules 0 findings across 44
files. pip-audit clean. DAST login check confirmed an authenticated session;
ZAP reached `10 URLs`, `FAIL-NEW: 0`.

Two unknowns resolved well: the hand-written Semgrep rules were authored against
`mark`'s code and could not be pre-tested on release's older `flaskapp` — they
report 0 there too; and release's 20 test files, which had never run in CI, all
pass.

### Trial run first, and the bug it found

The workflows were trial-run on a throwaway branch (`ci-pipeline-test`) before
being proposed, rather than merged on faith. That run **found a real bug**: the
advisory git-history secret scan was being silently *skipped*, because a step
following a failed step does not run unless it says `if: always()`. The blocking
tree scan failed on every run at the time, so the advisory scan had never once
executed. Fixed, and the fix is in PR #9.

**The same bug is still in `main`'s `security.yml`.**

### Two errors in the CI working notes, corrected

1. The notes claimed DAST would need a database seeding step once the sqlite
   file is untracked. It does not — `database.init_app()` calls `initialize()`
   then `seed_login_user()` on every app start, so the demo account exists
   against an empty database. One less prerequisite on the most expensive item.
2. The notes' CSRF-token extraction command could never have worked:
   `form.hidden_tag()` renders `type="hidden"` between the `name` and `value`
   attributes, so a pattern expecting `value=` immediately after `name=` matches
   nothing. Corrected, and proven by the now-passing login check.

### Still open

- **Merge PR #9**, then update the status line in `firstCIMergeToRelease.md`.
- **Delete the trial branch** `ci-pipeline-test` from the remote.
- **`instance/travel_planner.sqlite3`** — still tracked on `release` and `mark`.
- **Item 8** — which advisory checks should block. Needs the team.
- **The advisory scanners hide their failures.** A `continue-on-error` step shows
  green even when it found something. Of the 8 findings only visible in the
  uploaded reports, 6 are false positives; **2 are genuine** — `admin.html` loads
  bootstrap-icons and chart.js from `cdn.jsdelivr.net` with no `integrity=`
  attribute, so a compromised CDN would execute its code on the admin page.
- **Flight Agent guardrails need an adversarial regression corpus** (backlog
  item 11 / UR-075). The input and output gates exist and are enforced — see
  Part B §3.3 — but nothing proves they still catch attacks after someone edits
  a pattern. Static analysis cannot cover this; see the note below.
- Carried over: Duffel multi-airport fan-out still unexercised against the real API.
- **Resolved since 08-11:** the Azure key has been rotated, and `.env.secrets` is
  untracked on both `mark` and `release`.

---

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
| Obfuscated injection (base64, leetspeak, homoglyph, whitespace-split, non-English) | Pre-model, L2 | Blocked by the LLM classifier; the regex layer cannot see any of these |
| Role-play and hypothetical jailbreaks | Pre-model, L2 | Blocked by the LLM classifier |
| Out-of-scope and harmful requests | Pre-model, L2 | Blocked by the LLM classifier |
| Stereotyping phrased without a trigger word | Both, L2 | Blocked by the LLM classifier; the two-tier keyword rule misses these by construction |
| System-prompt leakage in the final plan | Post-model, L2 | Orchestrator output screened before it reaches the traveller |
| Fabricated booking or guarantee in the final plan | Post-model, L2 | Orchestrator output screened; retry once, then withhold |
| Guardrail unavailable | — | **Fail closed.** A classifier error blocks the request; `GUARDRAIL_FAIL_MODE=open` is available but explicit |

The L2 rows are the LLM classifier added on 2026-08-16 (Part A). Its measured
contribution is in [`docs/security/guardrail-eval-report.md`](security/guardrail-eval-report.md),
regenerated by `scripts/guardrail_eval.py` from the corpus in `tests/adversarial/`.

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
