# CI/CD pipeline — proposed changes (DRAFT, for team review)

Nothing in this folder is active. GitHub only runs workflows in
`.github/workflows/`, so these files do nothing until somebody moves them.
That is deliberate — the whole point is to agree the changes first.

Drafted 2026-08-09 against `.github/workflows/security.yml` @ `4d25531`.
Source backlog: `docs/security/cicd-owner-notes.md` (git-ignored owner notes).

---

## What's here

| Draft file | Destination when approved | Replaces |
|---|---|---|
| `ci-fast.yml` | `.github/workflows/ci-fast.yml` | the `secrets`, `tests`, `sast` jobs |
| `ci-dast.yml` | `.github/workflows/ci-dast.yml` | the `dast` job |
| `ci-evals.yml` | `.github/workflows/ci-evals.yml` | new — nothing today |
| `ci_stub_provider.py` | `scripts/ci_stub_provider.py` | new — nothing today |

Approving all of these means **deleting `.github/workflows/security.yml`**,
since its four jobs are redistributed across the first two files.

---

## The split, and why

Backlog item 17. The fast tier (secrets + tests + Semgrep) takes about two
minutes; ZAP is the slow, expensive part. A job cannot carry its own `on:`
trigger, so running the cheap checks everywhere while keeping DAST on
`main`/`release` means either `if:` conditions smeared across one file, or two
files. Two files reads better and lets each have its own concurrency group.

---

## Backlog items these drafts close

| Item | What changed | Confidence |
|---|---|---|
| 6 | `pull_request` triggers added to both tiers | **High** — one line, standard |
| 7 | `timeout-minutes` on every job (10/10/15/20/30) | **High** |
| 9 | Bandit `-ll` → `-l`, so LOW findings stop being silently dropped | **High** |
| 12 | Installs from `requirements.lock` when present, warns when not | **High** — but the lockfile itself still has to be generated |
| 14 | Random per-run `SECRET_KEY`; stub LLM provider | **Medium** — see caveats |
| 15 | Credential grep before upload; `retention-days: 14` everywhere | **High** |
| 17 | The split itself | **Decision, not code** |
| 13 | *Partial* — session assertion only, scan still unauthenticated | **Medium** |
| 18 | Tier 2 scheduled eval workflow | **Blocked on item 10** |

## What these drafts do NOT close

These are backlog items that are not workflow files, so they are not here:

- **Item 10 — network lockdown.** Needs `tests/conftest.py` and `pytest.ini`.
  I confirmed neither file exists anywhere in the repo today. `ci-fast.yml`
  already passes `-m "not live"` in anticipation; that flag is harmless until
  the marker is registered.
- **Item 11 — adversarial corpus.** Needs `tests/adversarial/injection_corpus.json`
  plus a parametrised test.
- **Item 16 — "OWASP ZAP" naming.** Documentation only; the workflow was
  already correct.
- **Item 8 — what severity gates.** A team decision. Where it applies I have
  left the current `continue-on-error` behaviour and commented the reasoning.

---

## Two corrections to the owner notes, found while drafting

**1. The DAST job does not need a database seeding step.**

Item 13 listed "a seeded login account" as prerequisite #1, on the assumption
that a user only exists because `instance/travel_planner.sqlite3` is committed,
and that untracking it would leave CI with an empty database and a failing
login.

That is wrong. `flaskapp/__init__.py` → `database.init_app()` calls
`initialize()` (creates the schema) and then `seed_login_user()` on **every**
application start, using `LOGIN_EMAIL` / `LOGIN_PASSWORD_HASH` from
`config.py`. The insert is `INSERT OR IGNORE`, so it is safe to repeat.

Untracking the sqlite file therefore does not break CI login, and no seeding
step is needed. One less prerequisite on the most expensive item.

Worth also knowing: the seeded demo account **is** an administrator, because
`ADMIN_EMAIL` defaults to `LOGIN_EMAIL` and `is_admin_email()` treats that
config value as authoritative regardless of the `users.is_admin` column. That
is why the session assertion can check `/api/v1/admin/activity` and expect
`200`.

**2. The CSRF-extraction snippet in the notes cannot work.**

The notes proposed:

```
grep -oP 'name="csrf_token" value="\K[^"]+'
```

The login form renders through `{{ form.hidden_tag() }}`, which emits
attributes in the order `id`, `name`, `type`, `value` — so `type="hidden"`
sits between `name=` and `value=` and the pattern matches nothing. The token
would come back empty and the step would fail for the wrong reason. Fixed in
`ci-dast.yml` to `name="csrf_token"[^>]*value="\K[^"]+`, plus an explicit
empty-token check so the failure message is honest either way.

---

## Caveats to discuss before merging

**`SESSION_COOKIE_SECURE` stays `false`, deliberately.** The owner notes
suggested setting it `true` so ZAP scans a realistic build. It would break the
scan: secure cookies are only transmitted over HTTPS and CI serves plain HTTP
on `127.0.0.1`, so the session cookie would never be sent back and login would
fail. The consequence to write into the report is that ZAP's *"cookie without
Secure flag"* finding is an artefact of CI configuration, not an app defect.
Doing better means terminating TLS in CI, which is not worth it here.

**The ZAP scan is still unauthenticated.** These drafts add the assertion that
a session *can* be established, which converts a silent failure into a loud
one. They do not add the ZAP Automation Framework migration or anti-CSRF token
configuration — that is the expensive half of item 13 and needs live iteration.
Until it lands, the honest report statement is that DAST covers the
unauthenticated surface only.

**The stub provider is partly verified.** Its schema synthesis is tested:
instances generated from `TravelPlan`, `AgentFinding` and
`FlightAgentResponse` all pass `model_validate()`. What is untested is the HTTP
layer — whether `langchain-openai` accepts the response envelope in practice.
Run it locally before merging; instructions are in the file's docstring.

**`ci-evals.yml` must not merge before item 10.** It is what introduces a real
provider key as a repository secret. Until `tests/conftest.py` clears provider
keys and blocks sockets, any unmocked test in the *blocking* fast suite could
silently make a live billed call and pass. The file carries this warning at the
top too.

---

## Decisions needed from the team

1. **Fast checks on every branch?** `ci-fast.yml` currently uses
   `branches: ['**']`. Costs shared Actions minutes; gives everyone feedback on
   their own branch. Narrow to a named list if preferred. *(Item 17.)*
2. **What gates a merge?** Currently blocking: pytest, gitleaks working-tree,
   LLM Semgrep rules. The open question is Bandit, pip-audit, registry Semgrep
   and ZAP. Recommended principle: gate on checks our code determines, stay
   advisory on checks someone else's determines. *(Item 8.)*
3. **Generate `requirements.lock`?** One command, and it unblocks making
   pip-audit blocking later. *(Item 12.)*
4. **Add `EVAL_LLM_API_KEY` as a repository secret?** Only after item 10.
   Use a spend-capped key dedicated to CI. *(Item 18.)*
5. **Delete `.github/workflows/security.yml`** as part of the same PR, or
   keep it briefly alongside for comparison?

---

## These drafts have now been run for real

Trialled on throwaway branch `ci-pipeline-test` (deleted after). `ci-evals.yml`
was excluded — it needs a provider secret and item 10 first.

| Job | Result | Time |
|---|---|---|
| Secret scan | **fail** — correct, see below | 22s |
| Tests | pass — `168 passed in 2.58s` | 40s |
| SAST | pass (advisory steps had findings) | 58s |
| DAST | pass, all steps | 2m2s |

Fast tier ~1m, DAST ~2m. The 10/10/15/20-minute timeouts are generous, as
intended — tighten later from real history.

**The secret scan failing is the gate working.** gitleaks reported
`leaks found: 1` against the tracked `.env.secrets`. `--redact` held: no key
value appeared in the logs. This stays red until `git rm --cached .env.secrets`
is committed.

### Things the run proved that could not be checked by reading

- **The stub provider works.** App started against it and served requests. No
  provider key was involved.
- **The corrected CSRF grep works.** `Authenticated session confirmed
  (admin/activity -> 200)`. This also confirms both corrections above: the
  demo user is auto-seeded, and it is an admin by default.
- **`-m "not live"` is harmless before item 10.** All 168 tests still ran.
- **The LLM Semgrep rules are clean.** `Ran 7 rules on 44 files: 0 findings`.
- **The artefact leak check passes today** — but only because `app.log` had no
  credential-shaped strings on an unauthenticated scan. It does not prove
  traveller content is absent.

### One bug the run found, now fixed

`Gitleaks (git history)` was **skipped**, not run. A step after a failed step
is skipped unless it says `if: always()`, and the blocking tree scan fails on
every run today. So the advisory history scan was silently dead. Fixed in
`ci-fast.yml` and confirmed by a second run.

**The same bug exists in the live `.github/workflows/security.yml`.** Worth
fixing there regardless of what happens to these drafts.

### What ZAP actually covered

`Total of 10 URLs`, `FAIL-NEW: 0  WARN-NEW: 10  PASS: 57`. Ten URLs is the
whole unauthenticated surface — login page plus static assets. This is the
hard number to quote for item 13 instead of describing the gap in prose.

### Findings the advisory scanners reported

`continue-on-error` makes a failing step show as green, so these were only
visible in the uploaded reports. Triaged:

| Finding | Count | Verdict |
|---|---|---|
| Bandit `B105 hardcoded_password_string` (LOW/MEDIUM) | 2 | **False positive.** `config.py:24` is a scrypt *hash*, and `:66` compares against the documented placeholder. |
| Semgrep `formatted-sql-query` + `sqlalchemy-execute-raw-query` | 4 | **False positive.** `database.py:192,201` build `ALTER TABLE` from hardcoded literal tuples; no user input reaches them, and SQLite cannot parameterise DDL identifiers. |
| Semgrep `missing-integrity` | 2 | **Genuine.** `admin.html:9,64` load bootstrap-icons and chart.js from `cdn.jsdelivr.net` with no `integrity=` attribute. |
| pip-audit | 0 | Clean — `No known vulnerabilities found`. |

Two consequences worth raising with the team:

1. **Item 9 is answered.** Lowering Bandit to `-l` produced 2 findings, both
   false positives. The volume is trivial, so reporting everything costs
   nothing. Gate at HIGH severity and silence these two with `# nosec`
   comments, or a Bandit baseline file.
2. **The pipeline found a real issue on its first run.** `missing-integrity`
   is the same class of supply-chain risk as item 4's unpinned actions — if
   jsdelivr served altered content, the admin page would execute it. Adding
   SRI hashes is a small fix and a genuinely good line for the report:
   the pipeline is not just ceremony.

### Still not verified

- Nothing has run on `main` or `release`, or through a `pull_request` event.
- ZAP has never scanned an authenticated route (the expensive half of item 13).
- `ci-evals.yml` has never run at all.
