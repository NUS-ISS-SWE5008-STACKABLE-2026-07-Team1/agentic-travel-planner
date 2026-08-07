# DAST Findings — 2026-07-25

**Source:** OWASP ZAP baseline scan (passive), run via GitHub Actions `Security` workflow
**Target:** `http://127.0.0.1:5000` (app booted with default/dev config in CI)
**Workflow run:** [30138857412](https://github.com/NUS-ISS-SWE5008-STACKABLE-2026-07-Team1/agentic-travel-planner/actions/runs/30138857412)
**Result:** 0 FAIL, 10 WARN, 3 additional informational alerts — pipeline currently blocked on this stage

This is a **passive baseline scan**: it crawls what's reachable without authenticating and inspects
responses. It found no confirmed exploit, but it does show the app is missing most of the standard
browser-side hardening headers. None of these require app logic changes — they're response headers
set once, app-wide.

## Summary

| Risk | Finding | Instances |
|---|---|---|
| Medium | Content-Security-Policy (CSP) header not set | 5 |
| Medium | Missing anti-clickjacking header (`X-Frame-Options` / `frame-ancestors`) | 3 |
| Low | `X-Content-Type-Options` header missing | 5 |
| Low | `Server` header leaks version info | 5 |
| Low | `Cross-Origin-Resource-Policy` header missing/invalid | 5 |
| Low | `Cross-Origin-Embedder-Policy` header missing/invalid | 3 |
| Low | `Cross-Origin-Opener-Policy` header missing/invalid | 3 |
| Low | `Permissions-Policy` header not set | 5 |
| Info | Storable and cacheable content (`/`, `/robots.txt`, `/sitemap.xml`) | 5 |
| Info | Storable but non-cacheable content (static assets) | 2 |
| Info | Session management token identified (informational, no fix needed) | 1 |
| Info | Authentication request identified (informational, no fix needed) | 1 |
| Info | "User Controllable HTML Element Attribute (Potential XSS)" hot-spot on login form (`email`, `password`, `csrf_token`, `submit`) — no attack payload or evidence recorded; ZAP flags this as a spot for manual review, not a confirmed issue. Jinja auto-escapes these fields when the login form re-renders after a failed attempt. | 4 |

## Details

### 1. Content-Security-Policy (CSP) header not set — Medium
Helps prevent XSS and data-injection by restricting which sources scripts/styles/frames can load from.
Affects: `/`, `/robots.txt`, `/sitemap.xml`.
**Fix:** set a `Content-Security-Policy` response header. Even a conservative default
(`default-src 'self'`) is a meaningful improvement over none.

### 2. Missing anti-clickjacking header — Medium
No `X-Frame-Options` or CSP `frame-ancestors` directive, so the login/dashboard pages can be framed
by another site (clickjacking risk). Affects: `/`.
**Fix:** set `X-Frame-Options: SAMEORIGIN` (or the CSP `frame-ancestors` equivalent).

### 3. `X-Content-Type-Options` header missing — Low
Without `nosniff`, older browsers may MIME-sniff responses and misinterpret content type.
Affects: `/`, static CSS/JS.
**Fix:** set `X-Content-Type-Options: nosniff`.

### 4. `Server` header leaks version info — Low
The `Server` response header discloses server/framework version, which helps an attacker fingerprint
known vulnerabilities. Affects: `/`, `/robots.txt`, `/sitemap.xml`, static assets.
**Fix:** suppress or genericize the `Server` header (e.g. via the WSGI server config).

### 5–7. `Cross-Origin-*-Policy` headers missing (COEP / COOP / CORP) — Low
Modern isolation headers that limit cross-origin data leakage. Not set anywhere.
**Fix:** set `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`,
`Cross-Origin-Resource-Policy: same-origin` (verify these don't break the Bootstrap CDN asset used in
`dashboard.html`, since CORP/COEP can block cross-origin resource loads).

### 8. `Permissions-Policy` header not set — Low
Restricts which browser features (camera, geolocation, etc.) the page can use. Not currently declared.
**Fix:** set a `Permissions-Policy` header disabling unused features, e.g.
`geolocation=(), camera=(), microphone=()`.

### 9–10. Cacheable content — Informational
Login/dashboard responses are cacheable by intermediate proxies. Since these pages carry session
context, consider `Cache-Control: no-store` on authenticated pages.

### 11–12. Session management / authentication request identified — Informational
ZAP correctly identified the login flow and session cookie. No action needed — these are detection
confirmations, not findings.

### 13. Potential XSS hot-spot on login form — Informational
Flagged because `email`/`password`/`csrf_token`/`submit` are user-supplied values reflected back into
the form. No exploit was demonstrated; Jinja's autoescaping covers this by default. Worth a quick
manual confirmation, not urgent.

## Suggested fix

All of the Medium/Low findings can be closed in one place: a Flask `after_request` hook (or the
`flask-talisman` package) in `flaskapp/__init__.py` that sets the response headers above app-wide.
This is app code, so it's being handed off rather than changed unilaterally.

## Full raw reports

Downloadable from the `zap_scan` artifact on the workflow run linked above (`report_html.html`,
`report_json.json`, `report_md.md`).
