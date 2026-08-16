# CI/CD improvement plan

**Status:** analysis only — **no pipeline changes have been made.**
**Created:** 2026-08-11
**Source:** comparison against the PeerConnect project's CI/CD write-up (§4.1.2–4.5.2),
supplied as a reference implementation from a previous project.

Kept separate from [`docs/progress.md`](../progress.md) deliberately: that file is a
session log, this is a standing plan that outlives any one session.

> **Before implementing anything here**, re-read §6 (Decisions still pending). Two
> unanswered questions determine whether half of this backlog is worth building at all.

---

## 1. What the PeerConnect pipeline is

Reduced to structure, it is four stages plus a governance layer.

| Stage | Frontend (React/Vite) | Backend (Java 21/Gradle) |
|---|---|---|
| Build | `npm ci` → `lint` → `build` | `./gradlew clean build` |
| Test + coverage | Vitest + V8 → lcov | JUnit + **JaCoCo, gated at 80% line & branch** |
| Analyse | SonarCloud | SonarCloud + **Snyk** (dependency CVEs) |
| Deploy | Azure Static Web Apps | Azure App Service (`app.zip`) |
| Post-deploy verify | Playwright E2E on live URL | Postman UAT + **k6 perf** on live URL |
| Runtime security | ZAP baseline, **nightly 02:00** on live URL | ZAP on live URL |

Governance: PR mandate, required status checks, one human reviewer per PR, GitHub
Copilot as a second automated reviewer, branch protection.

### The one idea worth taking from it

**It splits *pre-merge* checks from *post-deploy* checks.** Fast static analysis runs
on the code before merge; slow dynamic verification runs against a real running system
at a real URL afterwards.

Their entire UAT/DAST/k6 tier exists *because a deployed environment exists to point it
at*. That single fact decides most of what can transfer to this project.

---

## 2. Structural gap between the two projects

| | PeerConnect | agentic-travel-planner |
|---|---|---|
| Repos | 2 (frontend + backend) | 1 |
| Frontend | React/Vite, npm build | **Server-rendered Jinja + vanilla JS — no `package.json`** |
| Backend | Java 21 / Gradle | Python / Flask |
| **Deployed?** | **Yes — Azure SWA + App Service** | **No. Nothing is deployed anywhere.** |
| Coverage | JaCoCo, 80% gate | **None measured at all** |
| Dependency CVEs | Snyk | `pip-audit`, advisory only |
| Code quality | SonarCloud | Semgrep + Bandit |
| Secret scanning | not mentioned | **gitleaks, blocking** |
| LLM-specific rules | n/a | **7 custom Semgrep rules, blocking** |

Two consequences follow directly:

1. **The frontend pipeline is inapplicable.** There is no npm project here. `npm ci`,
   Vitest, `npm run build`, Static Web Apps deploy and `staticwebapp.config.json` have
   nothing to attach to — the "frontend" ships as Flask templates inside the same deploy
   unit as the backend.
2. **Everything they run "on the live URL" has no target.** Postman UAT, k6 and nightly
   ZAP all presuppose a deployment. Our DAST compensates by booting the app *inside* the
   CI runner and scanning `127.0.0.1:5000` — a legitimate substitute, and a more
   reproducible target than a shared environment.

---

## 3. Adopt

### Tier 1 — real gaps, roughly an hour in total

**1. Coverage measurement — the biggest genuine gap.**
261 tests, and no visibility into what they cover. `pytest-cov` is not even in
`requirements.txt`. Direct equivalent of their JaCoCo step.

Add measurement and an uploaded report **first**; set a threshold only once the real
number is known. Choosing 80% before measuring is how teams end up writing
assertion-free tests to hit a quota. Expectation: `flaskapp/travel_ai/` is likely
already well covered given the golden scenarios and bias audit.

**2. Branch protection with required status checks.**
Already tracked as open in the progress log. Note the sample has the *same* soft spot —
its wording is *"PRs are **expected** to be merged only when..."*, which is convention,
not enforcement. Ours would be genuinely stronger if the checks are actually required.
Needs repo admin: `Settings → Branches → Require status checks to pass before merging`.

**3. Dependabot** instead of Snyk. Native to GitHub, free, no account or token, and for
a Python repo it does Snyk's main job (dependency CVEs plus automatic update PRs).

**4. GitHub Copilot PR review.** Minutes to enable, and a governance line item the
report can point at.

### Tier 2 — worth it, some setup cost

**5. SonarCloud.** Overlaps Semgrep/Bandit on security but adds what they don't:
maintainability rating, duplication, cognitive complexity, and a dashboard. Free for
public repos. The dashboard is useful evidence for an academic deliverable.

**6. Promote `pip-audit` to blocking.** Currently `continue-on-error: true`. Once
Dependabot keeps dependencies current, gating on it costs little.

### Tier 3 — gated on the deployment decision

**7. Deploy to Azure App Service.** The unlock for everything below. App Service runs a
Flask app directly, so no containerisation is needed — that is what makes it cheap.

**8. Post-deploy UAT** (Playwright or Postman) against the deployed URL.

**9. Authenticated ZAP scan** — see §5.

**10. k6 performance testing**, scoped — see §4.

---

## 4. Do not copy

**k6 performance testing as-is.** PeerConnect's backend is CRUD over a database, so
throughput and p95 latency are meaningful. Our `/api/v1/travel-plans` fans out to **five
LLM agents**, so its latency is dominated by Azure OpenAI round-trips: a k6 run largely
measures someone else's service while burning real tokens and hitting rate limits. If
adopted, point it at the non-LLM surface (auth, dashboard, admin queries) and say so
explicitly. Load-testing the agent graph is a token-spend decision, not a CI decision.

**Splitting into two repositories.** Their split is driven by two genuinely different
toolchains. We have one.

**An 80% coverage gate on day one.** Measure first, then choose.

---

## 5. The login question

Two different problems get conflated here; they have different answers.

### Authenticating automated tests — largely solved

`docs/security/drafts/ci-dast.yml` already contains a working sequence: the app seeds
`demo@example.com` on every startup via `seed_login_user()` in `database.init_app()`, and
the draft extracts the CSRF token, POSTs a login, then asserts `/api/v1/admin/activity`
returns 200. It is marked **blocking**, with a sound rationale:

> *"Without this, a broken login degrades into a clean-looking report nobody questions."*

That is sharper thinking than anything in the PeerConnect document. Playwright or Postman
UAT can reuse the same flow.

### Getting ZAP to scan *as* a logged-in user — not solved

The draft is explicit that the scan is still `UNAUTHENTICATED`, so it only reaches the
login page. Everything behind auth — the planner, refinement, admin — goes unscanned.
Fixing it needs a ZAP context with authentication configuration or injected session
tokens. Real work, roughly half a day.

PeerConnect almost certainly shares this limitation: a ZAP *baseline* scan against a live
URL is unauthenticated by default, and their document does not claim otherwise.

### Open

**Which login problem is actually blocking?** If the product's login design is unsettled,
that is upstream of CI and blocks writing stable E2E tests, because selectors and flows
would churn. If it is only test authentication, that is mostly done and only the ZAP
piece remains.

---

## 6. Decisions still pending

1. **Is deployment in scope for this module?** The hinge question. Half the sample's
   value is post-deploy verification, unreachable without it. If deployment is out of
   scope, drop items 7–10 entirely rather than half-building them.
2. **Which login problem is blocking** — the product's login design, or test
   authentication? See §5.

---

## 7. Backlog

| # | Item | Effort | Blocked by |
|---|---|---|---|
| 1 | `pytest-cov` + coverage report artifact | ~30 min | — |
| 2 | Branch protection + required checks | ~10 min | repo admin |
| 3 | Dependabot | ~10 min | — |
| 4 | Copilot PR review | ~5 min | — |
| 5 | SonarCloud | ~1 h | account setup |
| 6 | Coverage *threshold*, once measured | ~15 min | #1 |
| 7 | Deploy to Azure App Service | ~half day | decision in §6.1 |
| 8 | Playwright/Postman UAT on live URL | ~half day | #7, §6.2 |
| 9 | Authenticated ZAP | ~half day | ZAP context config |
| 10 | k6, scoped to non-LLM endpoints | ~2 h | #7 |

Items 1–4 close the most real gaps for about an hour of work.

---

## 8. What this pipeline already does that the sample does not

Worth stating plainly, because the sample is more mature in *structure* and it is
tempting to treat it as strictly better. Four controls here have no counterpart there:

- **gitleaks secret scanning, blocking.** Not theoretical: a live Azure key was found in
  an unpushed commit on 2026-08-11 (see the progress log).
- **7 hand-written Semgrep rules for LLM/agent code** — no user data in a
  `SystemMessage`, no unstructured `.invoke()`, no `exec` sink in the agent package.
  Blocking, currently 0 findings across 49 files.
- **A bias audit and golden scenarios as CI gates.** PeerConnect has no equivalent
  because it has no model to audit.
- **Scheduled live-model evaluations**, drafted in `docs/security/drafts/ci-evals.yml`.

These exist because this is an agentic AI system and theirs is not. They belong
prominently in the report: this is where our pipeline is *ahead* of the sample, not
behind it. Do not drop them while adopting items from §3.

---

## 9. Current pipeline, for reference

`.github/workflows/security.yml` — runs on push to `main`, `release`, `mark`, on
`pull_request` to those branches, and on manual dispatch.

| Job | Blocking? | Notes |
|---|---|---|
| Secret scan — gitleaks working tree | **yes** | clean as of 2026-08-11 |
| Secret scan — gitleaks git history | no (advisory) | fails on `c309d7a`; key since rotated |
| Tests — pytest | **yes** | 261 passing |
| SAST — Semgrep `auto` | no (advisory) | 8 pre-existing findings, TEMP pending team review |
| SAST — Semgrep LLM/agent rules | **yes** | 0 findings |
| SAST — Bandit, pip-audit | no (advisory) | |
| DAST — ZAP baseline | no (`fail_action: false`) | app booted in-runner, unauthenticated |

Drafts not yet active, in `docs/security/drafts/`: `ci-fast.yml`, `ci-dast.yml`,
`ci-evals.yml`.
