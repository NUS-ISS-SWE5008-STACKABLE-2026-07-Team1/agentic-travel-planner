# Scanning the artefact, not just the source

Status: **experiment, phase 1.** Nothing in the pipeline depends on it.
Opened 19 October 2026.

## The gap this exists to close

An outside reviewer put it accurately: `ci-dast.yml` validates *a runtime
constructed from the commit*, not *the image that gets deployed*. The commit is
built twice, two different ways, and only the second one ships.

| | `ci-dast.yml` scans | GKE runs |
| --- | --- | --- |
| Server | Flask development server, `python app.py` | **gunicorn** + `gunicorn.conf.py` |
| Port | 5000 | 8000 |
| User | the runner's | **UID 10001** |
| Base image | the GitHub runner | `python:3.11.9-slim-bookworm` |
| Database | throwaway SQLite | Postgres |
| Graceful drain, metrics endpoint | absent | present |

Two consequences, and the second is the one people miss:

1. A green DAST is evidence about the **source**, not about the **artefact**.
2. Nothing in the pipeline looks at the image's OS and library layers **at all**.
   `pip-audit` covers `requirements.lock`; it says nothing about the Debian
   packages in the base image.

Neither of these is an argument that the current scan is worthless — it is the
only thing that catches an application-level regression before merge, and it
does that well. They are an argument that the claim it supports is narrower than
"we scan what we deploy."

## What this experiment does

`.github/workflows/ci-image-dast.yml` builds the image **once** and then uses
those same bytes for everything:

1. `docker build` → `travel-planner:local`.
2. Push to a **local registry** inside the runner to obtain a real digest.
   Workload Identity Federation is trusted for `release` only, so a feature
   branch cannot authenticate to Artifact Registry — and nothing should leave
   the runner for an experiment anyway.
3. **Assert the local tag and the pushed digest are the same image id.** Without
   this, "we scanned what we ran" is an assumption rather than a fact.
4. **Trivy** over the image layers. Advisory for now.
5. `docker compose up --no-build` — the real image, gunicorn, both roles,
   Postgres, the stub model.
6. The **existing** `.zap/authenticated-scan.yaml`, unchanged, against that
   container.
7. `scripts/zap_gate.py --min-risk medium` — the same gate and the same
   threshold as the live pipeline, so the two censuses are comparable.
8. Print the digest it **would** promote. Nothing is deployed.

## Why it was far cheaper than expected

Three pieces were already in place:

- **`deploy/docker-compose.yml` already runs the real image** — both roles,
  Postgres, and `scripts/ci_stub_provider.py` as the stub model — and publishes
  web on `127.0.0.1:5000`.
- **`.zap/authenticated-scan.yaml` already targets `http://127.0.0.1:5000`.**
  Compose publishes on exactly that address, so **the scan plan needs no changes
  at all.** The part that looked most expensive cost nothing.
- **`scripts/` is deliberately in the image** (`Dockerfile:37`), with a comment
  saying it is there for `scripts.a2a_server` and the compose stub.

The new code is one workflow, a four-line compose overlay, and this file. The
session handling, the gate and the scan plan are reused, not rewritten.

## How to read the result

The ZAP census from this job and the one from `ci-dast.yml` on the same commit
should be compared directly.

- **Identical** → the container exposes no additional surface at this threshold.
  Useful: it means today's source-level scan is a good proxy, and we can say so
  with evidence instead of hoping.
- **Different** → every difference is a finding, and specifically one the
  source-level scan is structurally incapable of seeing: gunicorn's header
  handling, the container's cookie behaviour, anything the development server
  did differently.

Baseline to compare against, `release` @ `bb33640`: **0 High, 0 Medium, 1 Low,
6 Informational.**

Trivy is expected to report a pile of base-image CVEs on first run. That is
information, not failure. The threshold gets chosen once the numbers exist —
the same way the ZAP gate was introduced on 19 October, rather than guessing a
level that either locks the branch or gates nothing.

## What this still does not prove

The same boundary as before, moved one step out, not removed:

- It scans **a container built from the commit**, not **the running deployment**.
  Real TLS, `Secure` cookies, the load balancer and Google IAP in front, the
  real Supabase database and the HPA's behaviour under load are all still
  outside it.
- `SESSION_COOKIE_SECURE` is explicitly `false`, because the scan speaks plain
  HTTP to `127.0.0.1` and a Secure cookie would simply never be sent. ZAP's
  "cookie without Secure flag" finding is a **CI artefact here** and must not be
  presented as an application defect.

The three-tier plan for scanning the deployment itself is unchanged and lives in
`docs/security/cicd-status.html`.

## Phase 2, which is not in this PR

Switching `deploy-gke.yml` to **promote this digest** rather than rebuilding the
commit a second way. That is the change that turns "we scanned the image" into
"we deployed the image we scanned", and it is also the change that can break
production. It is a separate PR, after the presentation, and only if phase 1
proves convincing.

Phase 2 would need, at minimum:

- the image pushed to Artifact Registry before the scan rather than after,
- `deploy/set_image.sh` taking a digest rather than a tag,
- `image-policy.yaml`'s admission rule updated, since its pattern currently
  requires `app:<tag>` and would **refuse** an `app@sha256:...` reference. This
  is a genuine trap: the policy has `failurePolicy: Fail`, so getting it wrong
  means the cluster rejects the deploy rather than failing quietly.

## If this is being thrown away

Delete the branch. Nothing else changed: no existing workflow, manifest, scan
plan or script was modified, and no credential or cloud permission was created.
