# Scanning the artefact, not just the source

Status: **in the pipeline, advisory.** `ci-image-dast.yml` runs on any pull
request that touches the image, and cannot block a merge.
Added 19 October 2026.

## The gap this closes

An outside reviewer put it accurately: `ci-dast.yml` validates *a runtime
constructed from the commit*, not *the image that gets deployed*. The commit was
built twice, two different ways, and only the second one shipped.

| | `ci-dast.yml` scans | GKE runs |
| --- | --- | --- |
| Server | Flask development server, `python app.py` | **gunicorn** + `gunicorn.conf.py` |
| Port | 5000 | 8000 |
| User | the runner's | **UID 10001** |
| Base image | the GitHub runner | `python:3.11.9-slim-bookworm` |
| Database | throwaway SQLite | Postgres |
| Graceful drain, metrics endpoint | absent | present |

Two consequences, and the second is the one that gets missed:

1. A green `ci-dast.yml` is evidence about the **source**, not the **artefact**.
2. Nothing looked at the image's OS and library layers **at all**. `pip-audit`
   covers `requirements.lock`; it says nothing about the Debian packages
   underneath.

Neither is an argument that the source-level scan is worthless — it is the only
thing catching an application regression before merge, and it does that well.
They are an argument that the claim it supported was narrower than "we scan what
we deploy."

## What this workflow does

`.github/workflows/ci-image-dast.yml` builds the image **once** and then uses
those same bytes for everything:

1. `docker build` → `travel-planner:local`.
2. Push to a **local registry** inside the runner to obtain a real digest.
   Workload Identity Federation is trusted for `release` only, so a feature
   branch cannot authenticate to Artifact Registry — and nothing should leave
   the runner for a scan anyway.
3. **Assert the local tag and the pushed digest are the same image id.** Without
   this, "we scanned what we ran" is an assumption rather than a fact.
4. **Trivy** over the image layers.
5. `docker compose up --no-build` — the real image, gunicorn, both roles,
   Postgres, the stub model.
6. The **existing** `.zap/authenticated-scan.yaml`, unchanged, against that
   container.
7. `scripts/zap_gate.py --min-risk medium` — the same gate and threshold as the
   live pipeline, so the two censuses are directly comparable.
8. Print the digest it **would** promote. Nothing is deployed.

### When it runs, and why it is advisory

On `pull_request` into `main`/`release`, filtered to the paths that decide what
the image *is* or how it is scanned: `Dockerfile`, `requirements.lock`,
`gunicorn.conf.py`, the compose files, the ZAP plan, the gate script, and the
workflow itself. Not `flaskapp/**` — application behaviour is already covered at
source level by `ci-dast.yml` on every PR, and listing it would make this run on
nearly everything.

**It is not in the required-check ruleset, and it must not be added to one while
it is path-filtered.** A path-filtered required check never reports on a PR that
misses the filter, so every such merge waits forever on a check that will never
arrive. Drop the filter first if it is ever to become required.

## Why it was cheap to build

Three pieces were already in place:

- **`deploy/docker-compose.yml` already runs the real image** — both roles,
  Postgres, and `scripts/ci_stub_provider.py` as the stub model — and publishes
  web on `127.0.0.1:5000`.
- **`.zap/authenticated-scan.yaml` already targets `http://127.0.0.1:5000`.**
  Compose publishes on exactly that address, so **the scan plan needed no
  changes at all.** The part that looked most expensive cost nothing.
- **`scripts/` is deliberately in the image** (`Dockerfile`), for
  `scripts.a2a_server` and the compose stub.

The session handling, the gate and the scan plan are reused, not rewritten.

## What the first run found

### 1. A Low that does not exist in production

Werkzeug advertises its version in the `Server` header; gunicorn does not. The
container scan reports **no Low at all**. The `Server` version-leak finding
carried on the board was an artefact of scanning the development server.

### 2. One Medium that is a CI artefact

ZAP `10106 HTTP Only Site`. The scan talks to a published container port where
there is no certificate and nothing terminating TLS; in the cluster TLS
terminates at the HTTPS load balancer behind IAP. Suppressed **in this workflow
only**, with the justification recorded at the suppression. `ci-dast.yml` does
not raise it, and an unnecessary suppression is one nobody reviews. Every
suppression prints as a visible `::warning::`.

`SESSION_COOKIE_SECURE` is `false` here for the same reason: the scan speaks
plain HTTP, and a Secure cookie would never be sent, breaking login entirely.

### 3. The base image had never been scanned

**11 CRITICAL, 120 HIGH, 218 MEDIUM** — every critical an OS package with a
published fix.

Two candidate fixes were built and scanned rather than argued about:

| | CRITICAL | HIGH | MEDIUM |
| --- | --- | --- | --- |
| Before | 11 | 120 | 218 |
| `apt-get upgrade`, keep 3.11.9 | **2** | 63 | 119 |
| Raise base tag to 3.11.17 | **2** | 63 | 117 |
| **Chosen: `apt-get upgrade` + `wheel`** | **2** | **62** | 119 |

A tie on everything that matters. **Chosen on blast radius, not counts:**
raising the tag also moves `PYTHON_VERSION` in `render.yaml`, changing the
interpreter on a deploy target no scan covers (Render builds with
`runtime: python` and never reads the Dockerfile), and jumps CPython eight patch
releases at once.

One detail decided it: the newer base image still had **two** fixable HIGHs
(`wheel`, `jaraco.context`) where `apt-get upgrade` plus an explicit
`setuptools` upgrade had one. **A newer base image does not fix the Python
tooling blind spot — only upgrading the tooling does.**

**Nothing fixable remains.** Filtering the report for entries with a published
fix returns an empty list. 9 of 11 criticals and 58 of 120 highs closed; the
surviving 64 have no upstream patch.

## How serious the remainder is

Almost all are **second-stage** vulnerabilities: they do not let anyone in, they
help someone already in. The front door is the Flask app and the agents, which
ZAP, SAST and the guardrails cover.

- **Not waved away:** `zlib1g` (CVE-2023-45853 — in `minizip`, a contrib
  component Debian does not ship as part of the library, which is *why* it is
  marked "no fix") and `libsqlite3-0`. Both are genuinely used code.
- **Genuinely inapplicable:** `libsystemd0` / `libudev1` (systemd does not run
  in the container), `util-linux`, `mount`, `ncurses`, `gzip` — local shell
  tools, which means an attacker has already won. This is the bulk of the 62.
- **What limits the damage:** UID 10001 non-root, Google IAP in front, a
  NetworkPolicy leaving `agents` with no public route, no secrets in the image.

**Stated rather than hidden:** this is reasoning about *reachability* from what
the application does, not a proof. A real exploitability assessment would trace
each library to actual call paths. That is out of scope here.

The real fix for the remaining 64 is a different base — distroless or Alpine.
Scoped, deliberately not attempted alongside this change.

## What this still does not prove

The same boundary as before, moved one step out, not removed. It scans **a
container built from the commit**, not **the running deployment**. Real TLS,
`Secure` cookies, the load balancer and IAP in front, the real Supabase database
and the HPA's behaviour under load are all still outside it.

The three-tier plan for scanning the deployment itself is unchanged and lives in
`docs/security/cicd-status.html`.

## Known gaps and next steps

- **Trivy is advisory** (`continue-on-error`). A threshold should be chosen now
  that the post-fix census exists — the same way the ZAP gate was introduced,
  census first.
- **`apt-get upgrade` is not reproducible.** Two builds of the same commit on
  different days can differ, which cuts against the pinning discipline
  everywhere else here. The better long-term answer is pinning the base image by
  **digest** and raising it deliberately on a schedule.
- **Phase 2:** having `deploy-gke.yml` promote this digest rather than rebuilding
  the commit a second way. That is what turns "we scanned the image" into "we
  deployed the image we scanned". It needs the image pushed to Artifact Registry
  before the scan, `deploy/set_image.sh` taking a digest, and
  **`deploy/k8s/image-policy.yaml` updated** — its pattern currently requires
  `app:<tag>` and would **refuse** `app@sha256:…`. With `failurePolicy: Fail`
  that means the cluster rejects the Deployment and the rollout never happens.
