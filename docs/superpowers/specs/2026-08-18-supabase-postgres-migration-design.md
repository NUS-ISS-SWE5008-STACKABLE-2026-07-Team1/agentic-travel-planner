# Supabase Postgres as the deployed database

Status: designed
Date: 2026-08-18

## Problem

`instance/travel_planner.sqlite3` is a file on the instance's local disk. On
Render that disk is ephemeral: every deploy and every free-tier spin-down
destroys registered users, saved plans, agent runs, audit events and admin
monitoring history. The app recovers — `init_app` recreates the schema and
reseeds the demo user on boot — so the loss is silent, which is worse than a
crash.

The database must outlive the instance. Supabase hosts the Postgres; the
application keeps its existing SQL.

## Scope

In scope: a dialect layer inside `flaskapp/database.py`, a Postgres schema,
configuration by `DATABASE_URL`, a one-off data migration for the 828 existing
rows, Postgres coverage in CI, and the Render wiring.

Out of scope, explicitly:

- **An ORM.** All 135 queries are written and working. Rewriting them as
  SQLAlchemy expressions is a far larger diff for the same result.
- **Changes to any calling module.** The eleven modules that import
  `flaskapp.database` call functions, never SQL, and none of them change.
- **Supabase Auth, RLS, storage or realtime.** Supabase is used strictly as
  hosted Postgres. Identity stays in the `users` table behind the existing
  Flask session.
- **Moving job state out of the in-memory dict** in `travel_ai/jobs.py`. That is
  what forces `--workers 1`, it is a separate piece of work, and this migration
  neither helps nor hinders it.

## Decisions

| Decision | Choice | Rejected alternative |
| --- | --- | --- |
| Vendor | Supabase | Render Postgres — free instance is deleted after 30 days |
| Abstraction | Thin dialect shim over raw SQL | SQLAlchemy Core; two divergent copies of every query |
| Connection endpoint | Supabase **session pooler** DSN | Direct connection — IPv6-only, unreachable from Render |
| Selector | `DATABASE_URL` set → Postgres, else SQLite path | A `DB_BACKEND` flag naming the dialect explicitly |
| Local dev and the 308 tests | Stay on SQLite | Postgres everywhere via Docker |
| Production dialect coverage | A CI job against `postgres:16` | Trusting that the two dialects agree |
| Existing data | Migrated, keys preserved | Fresh start |
| Case-insensitive email | `CITEXT` | Plain `TEXT UNIQUE`, relying on callers lowercasing |
| Dashboard date grouping | `SUBSTR` on the fixed timestamp format, one query text for both engines | Per-dialect copies of the four aggregation queries |
| Import-time `app = create_app()` | Deleted | Kept, with tests forced to override `DATABASE` |

## Dialect differences that actually bite

Seven, found by survey rather than assumed. The first two are the ones that
would have shipped as silent bugs.

**1. `CURRENT_TIMESTAMP` formats differently.** Every `created_at` is a `TEXT`
column defaulting to `CURRENT_TIMESTAMP`. SQLite writes `2026-08-18 14:30:00`.
Postgres writes `2026-08-18 14:30:00.123456+00`. The admin dashboard sorts and
slices these as strings, and `idx_jobs_submitted` orders on them, so a format
change silently reorders the monitoring view and breaks the `%Y-%m` grouping in
`get_admin_token_summary`. The Postgres schema therefore defaults to
`to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')`, matching SQLite
byte for byte.

**2. `with connect(...)` means different things.** `sqlite3` treats it as a
transaction scope: commit on exit, connection left open. `psycopg` treats it as
a connection scope: commit *and close*. `database.py` uses this pattern in ~20
functions. The shim makes both commit-and-close, which is what the code already
assumes.

**3. Placeholders.** 135 `?` markers; Postgres wants `%s`. The wrapper rewrites
them at execute time so the query strings stay as written.

**4. Generated keys.** 7 tables use `INTEGER PRIMARY KEY AUTOINCREMENT` and read
`cursor.lastrowid`. Postgres uses `GENERATED ALWAYS AS IDENTITY` and
`RETURNING id`.

**5. Upserts.** `INSERT OR IGNORE` (in `seed_login_user`, `a2a_messages`,
`audit_events`) becomes `ON CONFLICT DO NOTHING`.

**6. Introspection.** The three `initialize()` migration blocks read
`PRAGMA table_info(...)`; Postgres uses `information_schema.columns`.

**7. Case-insensitive email.** `email TEXT NOT NULL COLLATE NOCASE UNIQUE` has
no Postgres equivalent. `CITEXT` (a Supabase-available extension) preserves the
semantics exactly. Callers already lowercase, so this is defence in depth
rather than the only guard.

**8. A DSN routed through `Path()`.** Not a SQL difference, but the most
dangerous item found. `api.py:75` puts `str(config["DATABASE"])` into the job
settings and `jobs.py:63` reconstitutes it as `Path(settings["database_path"])`.
`Path("postgresql://host/db")` normalises the double slash away, yielding
`postgresql:/host/db` — no longer a DSN, and no longer recognised as one. The
background planning thread would then fall through to SQLite and write plans,
findings, agent runs and audit events into a stray local file, while the web
request path talked to Supabase correctly. Nothing would raise. The fix is to
stop coercing: `database_path` is passed through as the string it already is,
and `TravelPlanningService`'s annotation widens to `Path | str | None`.

**9. SQLite-only date functions in the admin dashboard.** Found while planning;
an earlier survey missed them by grepping only lowercase. `get_admin_token_summary`
and `get_platform_dashboard` use `DATE(...)` (15 uses) and `STRFTIME('%Y-%m', ...)`
(6 uses) — neither exists in Postgres with those semantics. The literal `%` is a
second problem: psycopg reads `%` as the start of its own placeholder, so these
queries would fail to bind even after `?` translation.

Both are resolved without dialect-specific SQL. Because decision 1 fixes the
stored format at `YYYY-MM-DD HH:MM:SS` in both engines, these are string slices:

| SQLite-only | Both engines |
| --- | --- |
| `DATE(x)` | `SUBSTR(x, 1, 10)` |
| `STRFTIME('%Y-%m', x)` | `SUBSTR(x, 1, 7)` |

`SUBSTR` behaves identically in SQLite and Postgres, ISO-8601 strings sort
lexicographically so `>=` and `ORDER BY` are unchanged, and no `%` remains in
any query. This makes the two dashboard functions dialect-neutral rather than
duplicated, and is why the shim needs only placeholder translation and not a
SQL rewriter.

**10. `flaskapp/__init__.py` builds an app at import time.** Line 32 is
`app = create_app()`, and `create_app` calls `init_app`, which calls
`initialize()`. Today that quietly creates a SQLite file. Once `DATABASE_URL`
exists in `.env.secrets`, *importing the package at all* — which every one of
the 308 tests does — would connect to Supabase and run schema DDL against the
production database. The test suite would go from offline to hitting the
network, and `initialize()` would run against live data on every test session.

Nothing depends on that export: no module imports `from flaskapp import app`,
its only mention is its own explanatory comment, and `render.yaml` starts
gunicorn with `flaskapp:create_app()`. It is deleted, which must happen
*before* `DATABASE_URL` is honoured.

Also noted: `save_plan` inserts into `a2a_messages` positionally —
`INSERT OR IGNORE INTO a2a_messages VALUES (?, ...)` with 11 unnamed values.
Column *order* is therefore load-bearing across both schemas, and the schema
parity test below exists partly to catch that.

## Components

### `flaskapp/database.py` — dialect layer

Added above the existing code, roughly 80 lines:

```
is_postgres(target)      -> bool     # target is a str starting postgresql://
connect(target)          -> Connection wrapper (Path -> sqlite3, DSN -> psycopg)
```

The wrapper exposes exactly what the existing code uses — `execute`, `commit`,
`rollback`, `close`, context-manager entry/exit — plus a `dialect` attribute and
an `insert_returning_id(sql, params)` helper replacing `cursor.lastrowid`. Rows
come back as mappings supporting both `row["name"]` and `dict(row)`, as
`sqlite3.Row` does today.

`SCHEMA` becomes `SCHEMA_SQLITE` (today's text, unchanged) and
`SCHEMA_POSTGRES`. `initialize()` picks one, and its three additive-column
migration blocks branch on dialect for introspection only — the `ALTER TABLE`
statements are identical in both.

`IntegrityError` is re-exported as a dialect-neutral name so `create_user`'s
`except` clause catches the right thing on both.

### `flaskapp/config.py`

```python
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE = DATABASE_URL or Path(os.getenv("DATABASE", "instance/travel_planner.sqlite3"))
```

One line of behaviour: absent `DATABASE_URL`, every existing local and test path
is bit-for-bit what it is today. `.env.example` documents the variable and
points at the session pooler.

### `flaskapp/travel_ai/jobs.py` and `service.py` — stop coercing the target

`jobs.py:63` passes `settings["database_path"]` through unchanged instead of
wrapping it in `Path(...)`, and `TravelPlanningService.__init__` annotates
`database_path` as `Path | str | None`. `trace_dir` keeps its `Path(...)` — it
is a real directory in both configurations. Two lines, and without them the
background planning path silently bypasses Postgres entirely.

A regression test covers exactly this: submit a job with a `postgresql://`
`DATABASE`, and assert the target reaching `save_plan` is still a valid DSN.

### `scripts/migrate_sqlite_to_postgres.py`

One-off, idempotent, safe to re-run after a partial failure.

- Reads a SQLite path, writes to a `DATABASE_URL`, both from argv/env.
- Copies tables in foreign-key order: `users`, `travel_requests`,
  `travel_plans`, `agent_findings`, `options`, `a2a_messages`, `audit_events`,
  `planning_jobs`, `intake_messages`, `agent_runs`, `plan_feedback`.
- Preserves integer primary keys explicitly, so every foreign key stays valid.
- Calls `setval` on each identity sequence afterwards. Without this the first
  new insert collides with a migrated id — the single most likely way this
  script leaves a broken database behind.
- `ON CONFLICT DO NOTHING` throughout.
- Copies `audit_events` verbatim, so `verify_hash_chain` still passes on
  migrated traces.
- Prints a per-table source/destination row count and exits non-zero on any
  mismatch, so "it worked" is verified rather than assumed.

### `.github/workflows/security.yml`

The `tests` job gains a `postgres:16` service and `TEST_DATABASE_URL`. The
default suite still runs on SQLite; the Postgres tests below stop being skipped.

### `render.yaml`

`DATABASE_URL` added as `sync: false`. The commented persistent-disk block is
deleted — with state in Supabase the ephemeral disk no longer matters, which
makes the free instance type viable again.

### `requirements.txt`

`psycopg[binary]>=3.2,<4`.

## Error handling

- **No `DATABASE_URL`** — SQLite, exactly as today. This is the local default,
  not a failure.
- **Unreachable Postgres at boot** — `init_app` calls `initialize()` at import
  time, so the worker fails to start and Render's health check fails the deploy.
  That is the correct outcome: a running app silently writing nowhere is worse.
  The error message must name the pooler-vs-direct distinction, because that is
  the likely cause.
- **Migration script partway** — re-running is safe; counts are reported per
  table and a mismatch is a non-zero exit.

## Testing

- The 308 existing tests are untouched and keep running on SQLite via `tmp_path`.
- `tests/test_database_postgres.py`, skipped unless `TEST_DATABASE_URL` is set:
  schema creation, `save_plan` round-trip, `initialize()` idempotency, the
  additive-column migration path, `create_user` duplicate-email rejection, and
  the admin aggregation queries.
- `tests/test_schema_parity.py`: both schema constants declare the same tables,
  the same column names per table, and the same column *order* — the last of
  which protects the positional `a2a_messages` insert.
- A timestamp-format test asserting both dialects produce `YYYY-MM-DD HH:MM:SS`
  for a defaulted `created_at`.
- Manual, once: deploy, register a user, run a plan, redeploy, confirm the user
  and plan survive.

## Known limitations

- Local dev and Postgres can still drift in ways no test covers; the CI job
  narrows this but does not close it. This is the accepted cost of keeping
  SQLite for local work.
- The connection-per-call pattern (`with connect(path)`) opens and closes a
  Postgres connection per operation, which is slower than a pool. At one worker,
  eight threads and a four-thread planning executor this is well within
  Supabase's connection limits and not worth a pool yet.
- Supabase free projects pause after roughly a week of inactivity and need a
  dashboard click to resume. Fine for coursework, not for anything graded on
  uptime.
- Nothing here changes the `--workers 1` constraint.
