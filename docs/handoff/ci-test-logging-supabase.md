# CI test-result logging to Supabase — code live, one-time setup left

**Status (2026-09-16):** the code is on `release` and runs on every CI run, but
**logs nothing yet**. Until the `TEST_RESULTS_DATABASE_URL` secret exists, both
logging steps print a notice and move on. Setup below is ~10 minutes, done by a
person, in two web UIs. Nothing else is outstanding.

Replaces the 2026-09-06 version of this note, which was "paused, waiting on
Subra". That question — is production actually on Supabase? — was answered on
2026-09-14 via the Supabase MCP connector: yes, the app's tables live in
`Travelplanner_schema` (project ref `kgwanhhqlvozuxnwyhml`).

## What it does

`scripts/log_test_results.py` reads the pytest JUnit report from the `tests`
and `e2e` jobs in `.github/workflows/ci-checks.yml` and writes:

- `ci_test_runs` — one row per job run: workflow, job, branch, commit, run URL,
  totals, duration.
- `ci_test_results` — one row per test: name, outcome, duration, failure text
  (or skip reason), truncated at 4000 characters.

Both tables go in `Travelplanner_schema`, set by `TEST_RESULTS_DATABASE_SCHEMA`
in the workflow. The script never touches app tables and never fails a build:
missing secret, unreachable database, or a bad report all end in a printed
notice and exit 0.

## What changed from the 6 Sep version

| Change | Why |
|---|---|
| Tables go into a named schema (`SET search_path`, quoted) | They used to land in the server default (`public`), away from every other table this project owns |
| DDL runs only when the tables are missing | Postgres checks CREATE permission *before* `IF NOT EXISTS`, so the old unconditional `CREATE TABLE IF NOT EXISTS` failed for an insert-only login even with both tables present. Proven by `test_an_insert_only_login_can_log_once_the_tables_exist` going red when the check is removed |
| Both inserts in one transaction | A failure part-way could leave a run row with missing results |
| 10-second connect timeout | An unreachable database must not hold a CI job open |
| Branch uses `GITHUB_HEAD_REF` on pull requests | `GITHUB_REF_NAME` is `22/merge` there, not a branch name |
| `tests/test_log_test_results_postgres.py` | The database half had only ever been checked by hand. Now runs in CI against the `postgres:16` service, including a real insert-only role |

## One-time setup

Use an **insert-only login**, not the app's own `DATABASE_URL`. If the CI
secret ever leaked, it could then add junk test rows and nothing else — no
reading `users`, no touching plans.

### 1. Supabase → SQL Editor

Pick a password first. Letters and digits only, so it never needs
percent-encoding in a connection string. Replace `CHANGE_ME` below, run once:

```sql
-- Tables, created by the owner so the CI login never needs CREATE.
CREATE TABLE IF NOT EXISTS "Travelplanner_schema".ci_test_runs (
    id BIGSERIAL PRIMARY KEY,
    workflow TEXT NOT NULL,
    job TEXT NOT NULL,
    branch TEXT,
    sha TEXT NOT NULL,
    run_url TEXT,
    ran_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    total INT NOT NULL,
    passed INT NOT NULL,
    failed INT NOT NULL,
    skipped INT NOT NULL,
    duration_seconds NUMERIC
);
CREATE TABLE IF NOT EXISTS "Travelplanner_schema".ci_test_results (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES "Travelplanner_schema".ci_test_runs(id) ON DELETE CASCADE,
    test_name TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_ms INT NOT NULL,
    failure_message TEXT
);
CREATE INDEX IF NOT EXISTS ci_test_results_run_id_idx
    ON "Travelplanner_schema".ci_test_results(run_id);
CREATE INDEX IF NOT EXISTS ci_test_results_test_name_idx
    ON "Travelplanner_schema".ci_test_results(test_name);

-- The CI login: insert and read these two tables, nothing else.
CREATE ROLE ci_test_logger LOGIN PASSWORD 'CHANGE_ME';
GRANT USAGE ON SCHEMA "Travelplanner_schema" TO ci_test_logger;
GRANT SELECT, INSERT ON "Travelplanner_schema".ci_test_runs,
                        "Travelplanner_schema".ci_test_results TO ci_test_logger;
GRANT USAGE ON SEQUENCE "Travelplanner_schema".ci_test_runs_id_seq,
                         "Travelplanner_schema".ci_test_results_id_seq TO ci_test_logger;
```

`SELECT` is there because `INSERT ... RETURNING id` needs it. The same grants
are exercised in CI by `tests/test_log_test_results_postgres.py`.

### 2. Build the connection string

Supabase → Project Settings → Database → Connection string → **Session
pooler**. Copy it. Its username already looks like `postgres.kgwanhhqlvozuxnwyhml`:
keep the `.kgwanhhqlvozuxnwyhml` part, change only `postgres` before the dot to
`ci_test_logger`, and put in the new password:

```
postgresql://ci_test_logger.kgwanhhqlvozuxnwyhml:<password>@<pooler host>:5432/postgres
```

Session pooler, not the direct connection — same IPv6 reason CLAUDE.md gives
for Render; GitHub's runners have the same limitation.

### 3. GitHub → repo Settings → Secrets and variables → Actions

New repository secret, named exactly `TEST_RESULTS_DATABASE_URL`, value from
step 2. Use the web UI, not a terminal command or a chat, so the credential
never lands in a shell history or a transcript.

### 4. Check it worked

On the next push to `release`, open the run → `tests` job → **Log test results
to Supabase**. It should say `Logged run N (tests): ...`. Then in the SQL Editor:

```sql
SELECT id, job, branch, left(sha, 7) AS sha, passed, failed, skipped, ran_at
FROM "Travelplanner_schema".ci_test_runs ORDER BY id DESC LIMIT 10;
```

If the step prints `::warning::Test-result logging failed`, the message after
it says why; the build is unaffected either way.

## Things to know

- **Security advisor.** Supabase will flag both new tables as "RLS disabled",
  like the 14 app tables already are. Same low practical risk: nothing in this
  project uses the Supabase client library or an anon key. Turning RLS on would
  block `ci_test_logger` unless you also add an insert policy for it.
- **GKE schema.** A second schema, `travelplanner_gke`, exists for the GKE
  test. CI logging deliberately uses only `Travelplanner_schema`.
- **Growth.** One result row per test per push (several hundred), a few KB each.
  Small against the free tier, but if it ever matters:
  `DELETE FROM "Travelplanner_schema".ci_test_runs WHERE ran_at < now() - interval '90 days';`
  (results are removed with their run by `ON DELETE CASCADE`).
- **Forks.** GitHub never gives secrets to pull requests from forks, so those
  runs print the notice. Not a concern for this private repo.
