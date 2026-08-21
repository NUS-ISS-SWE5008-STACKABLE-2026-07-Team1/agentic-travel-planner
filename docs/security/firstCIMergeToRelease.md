# First CI/CD landing on `release`

**Date:** 2026-08-15
**PR:** [#9 — ci: bring the split security pipeline to release](https://github.com/NUS-ISS-SWE5008-STACKABLE-2026-07-Team1/agentic-travel-planner/pull/9)
**Status at time of writing:** OPEN, mergeable, all checks green
**Branch:** `ci/pipeline-release` → `release` · commit `8a68a09` · based on `release` @ `9434559`

A record of what was changed, why, and what it produced. Written so someone
who was not in the conversation can follow it.

---

## Why now, and why `release` first

`release` had **no CI at all**. Its `security.yml` only triggered on push to
`main`, and it carried no test job and no secret-scanning job.

The team is merging feature branches into `release` in a few days. Landing the
pipeline *before* those merges means they arrive gated. Landing it afterwards
would mean several people's work merges untested, CI goes red, and nobody can
tell whose change caused it — the exact problem the pipeline exists to prevent.

This is why the ordering matters: for `pull_request` events GitHub reads the
workflow from the *merged* result, so once the trigger is on `release`, every
future PR into it is checked automatically, wherever it came from.

---

## What changed

| File | Action | Why |
|---|---|---|
| `.github/workflows/ci-fast.yml` | added | secrets + tests + SAST, ~1 min. Runs on each member's own branch and on PRs into `main`/`release`. |
| `.github/workflows/ci-dast.yml` | added | the slow website scan. `main`/`release` only. |
| `.github/workflows/security.yml` | **deleted** | superseded by the two above. Keeping it would run every scan twice. |
| `.semgrep/llm-agent.yml` | added | `ci-fast.yml` gates on these rules and they existed only on `mark`. Without this the blocking step fails on a missing file. |
| `scripts/ci_stub_provider.py` | added | a fake AI provider, so the DAST job can exercise the app with **no real API key**. |
| `requires.txt` | **deleted** | backlog item 5. Contained only `-r requirements.txt`; a repo-wide search found no references. |

**Deliberately not touched:** `instance/travel_planner.sqlite3` remains tracked,
pending a team decision. It holds 2 user rows and 18 travel-request rows
carrying traveller gender and age data.

### Splitting one workflow into two

Backlog item 17. The quick checks take about a minute; the website scan is the
slow part. A single job cannot choose its own branches, so running the quick
tier widely while keeping the slow tier narrow requires two files.

---

## Team decisions included

| Item | Decision | Effect |
|---|---|---|
| 5 | Delete `requires.txt` — nobody uses it | file removed |
| 6 | Run checks on pull requests, `main` + `release` only for now | `pull_request` trigger added |
| 7 | Accept the proposed time limits | 10 min secrets, 10 tests, 15 SAST, 20 DAST. Default was **6 hours**. |
| 9 | Show low-severity Bandit findings first | `-ll` → `-l` |
| 17 | Quick checks on everyone's own branch | runs on all 7 active branches, named explicitly rather than a wildcard |

Item 8 (which failures should block a merge) is still open and needs the team.

---

## Results

Both workflows ran on the `pull_request` event, so the PR tested the very
trigger it was adding.

| Check | Result | |
|---|---|---|
| Secret scan — working tree | `no leaks found` | **first time green** |
| Secret scan — history | `leaks found: 1` | advisory, permanently red by design |
| Tests | `168 passed in 2.76s` | |
| Custom AI rules | `Ran 7 rules on 44 files: 0 findings` | blocking |
| Semgrep registry rules | `6 findings` | advisory |
| Bandit | 2 findings | advisory, both false alarms |
| pip-audit | `No known vulnerabilities found` | |
| DAST — login check | `Authenticated session confirmed (admin/activity -> 200)` | blocking |
| DAST — ZAP | `Total of 10 URLs`, `FAIL-NEW: 0`, `WARN-NEW: 10`, `PASS: 57` | advisory |
| DAST — log leak check | clean | |

Timings: fast checks 52s, DAST 1m42s. The agreed limits leave plenty of room.

**On the green secret scan.** The blocking check passes because the Azure key
was rotated and `.env.secrets` untracked before this PR. The history scan still
reports 1 and always will — the key is in past commits. That is an accepted,
documented state, not a broken build. Do not "fix" it by deleting the step.

**Two unknowns that resolved well.** The custom Semgrep rules were written
against `mark`'s code and could not be pre-tested on release's older
`flaskapp` — they report 0 findings there too. And release's 20 test files,
which had never run in CI, all pass.

---

## Validation done beforehand

The workflows were trial-run on a throwaway branch (`ci-pipeline-test`) before
this PR, rather than proposed untested. That trial:

- confirmed the fake AI provider works, and the automatic login check succeeds
- confirmed 168 tests pass and the custom AI rules report 0 findings
- **found a real bug**: the advisory git-history scan was being silently
  skipped, because a step following a failed step does not run unless it says
  `if: always()`. Since the blocking scan failed on every run at the time, that
  advisory scan had never actually executed. Fixed, and the fix is in this PR.

Two errors in the original working notes were also corrected while drafting:

1. The notes claimed DAST would need a database seeding step. It does not —
   `database.init_app()` seeds the demo user on every app start.
2. The notes' CSRF-token extraction command could never have worked, because
   the form renders `type="hidden"` between the `name` and `value` attributes.
   Corrected, and proven by the passing login check.

---

## Known limitations, stated rather than hidden

- **The website scan only reaches the login page.** `Total of 10 URLs`.
  Everything behind authentication — travel plans, admin pages, saved reasoning
  traces — is untested. A clean result here means "the scanner could not get
  in", not "the app is safe". This is backlog item 13 and the biggest remaining
  job.
- **Advisory checks hide their failures.** A step set to "report, don't block"
  shows green even when it found something. The 6 registry-Semgrep findings and
  2 Bandit findings only appear in the uploaded reports. Of those 8: 6 are false
  positives, and 2 are genuine — `admin.html` loads scripts from a CDN with no
  integrity check, so a compromised CDN would run its code on the admin page.
- **No real AI is ever tested.** `ci-evals.yml` exists as a draft but is not
  included: it needs a provider key as a repository secret, and backlog item 10
  (blocking network access during tests) must land first.

---

## What the team will see after merging

`release` had no CI before this, so feature branches will hit real checks for
the first time. Expect:

- the history secret scan permanently red — expected, explained above
- 6 registry-Semgrep and 2 Bandit findings — all advisory, none blocking
- the blocking checks are: tests, working-tree secret scan, custom AI rules,
  and the DAST login check

---

## Still outstanding

| Item | What |
|---|---|
| — | Decide whether to untrack `instance/travel_planner.sqlite3` |
| 8 | Agree which advisory checks should start blocking |
| 10 | Stop tests reaching the real AI — **must precede any real API key** |
| 11 | Attack-phrase list for the AI safety filters |
| 12 | Pin library versions with a lock file |
| 13 | Scan the app while logged in |
| 15 | Check saved logs for traveller data before publishing them |
| 16 | Fix "OWASP ZAP" wording in the written report |
| 18 | Weekly real-AI test — after item 10 |

Full detail: `docs/security/cicd-status.html` and `docs/security/drafts/README.md`.

---

## Reverting

Nothing is merged yet. To back out entirely:

```bash
gh pr close 9
git push origin --delete ci/pipeline-release
```

After merging, a single revert commit on `release` restores the previous state,
including `security.yml` and `requires.txt`.

## Cleanup still to do

The trial branch `ci-pipeline-test` is still on the remote and no longer needed:

```bash
git push origin --delete ci-pipeline-test
```
