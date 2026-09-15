"""Log one CI job's pytest outcomes to Supabase/Postgres, for history beyond
GitHub Actions' 14-day artifact retention.

Parses a JUnit XML report (`pytest --junitxml=...`) and writes one row to
`ci_test_runs` plus one row per test case to `ci_test_results`. Deliberately
its own table pair: never touching `users`, `travel_requests`, or any other
app table, so a bug here cannot corrupt product data.

Configuration (both read from the environment):

- `TEST_RESULTS_DATABASE_URL` — a GitHub Actions secret holding a Postgres
  connection string. For Supabase, the SESSION POOLER string, same requirement
  as `DATABASE_URL` (see CLAUDE.md's Database section). Use a login that can
  only insert into these two tables, not the app's own `DATABASE_URL` login;
  `docs/handoff/ci-test-logging-supabase.md` has the SQL.
- `TEST_RESULTS_DATABASE_SCHEMA` — which schema the tables live in. Applied
  per connection with `SET search_path`, for the same pgbouncer reason
  `flaskapp/database.py` gives for `DATABASE_SCHEMA`. Empty means the
  server's default schema.

The tables are created on first use, but only when they do not already exist.
That order matters for a least-privilege login: Postgres checks CREATE
permission on the schema BEFORE it checks `IF NOT EXISTS`, so issuing
`CREATE TABLE IF NOT EXISTS` unconditionally fails for a login that may only
insert, even when both tables are already there.

This must never fail a CI job: an unset secret, an unreachable database, or a
malformed report all degrade to a printed notice and exit 0. Logging is
observability, not correctness — a Supabase outage should never turn a green
test run red.
"""

from __future__ import annotations

import argparse
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

MAX_FAILURE_MESSAGE_CHARS = 4000

# Seconds. An unreachable database must not hold a CI job open for the OS's
# much longer default TCP timeout.
CONNECT_TIMEOUT_SECONDS = 10

TABLES = ("ci_test_runs", "ci_test_results")

DDL = """
CREATE TABLE IF NOT EXISTS ci_test_runs (
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

CREATE TABLE IF NOT EXISTS ci_test_results (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES ci_test_runs(id) ON DELETE CASCADE,
    test_name TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_ms INT NOT NULL,
    -- Doubles as the skip reason for outcome='skipped' — "why wasn't this a
    -- plain pass" is the same question either way, and a skip reason (e.g.
    -- "Postgres suite skipped, TEST_DATABASE_URL unset") is exactly the kind
    -- of thing worth keeping history of.
    failure_message TEXT
);

CREATE INDEX IF NOT EXISTS ci_test_results_run_id_idx ON ci_test_results(run_id);
CREATE INDEX IF NOT EXISTS ci_test_results_test_name_idx ON ci_test_results(test_name);
"""


@dataclass
class TestResult:
    __test__ = False  # a data class, not a pytest test class

    name: str
    outcome: str  # "passed" | "failed" | "skipped"
    duration_ms: int
    failure_message: str | None = None


@dataclass
class RunSummary:
    total: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    results: list[TestResult] = field(default_factory=list)


def parse_junit_xml(path: str) -> RunSummary:
    """Read a pytest `--junitxml` report.

    pytest's root is `<testsuites>` wrapping one `<testsuite>` — the same
    shape `ci-checks.yml`'s "Confirm the Postgres suite actually ran" step
    already assumes, matched here for consistency.
    """
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        raise ValueError(f"{path}: no <testsuite> element found")

    results: list[TestResult] = []
    for case in suite.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        full_name = f"{classname}::{name}" if classname else name
        duration_ms = int(float(case.get("time", "0")) * 1000)

        skipped = case.find("skipped")
        failure = case.find("failure")
        error = case.find("error")
        if skipped is not None:
            outcome, message = "skipped", skipped.get("message")
        elif failure is not None or error is not None:
            node = failure if failure is not None else error
            message = (node.text or node.get("message") or "").strip() or None
            if message and len(message) > MAX_FAILURE_MESSAGE_CHARS:
                message = message[:MAX_FAILURE_MESSAGE_CHARS] + "... [truncated]"
            outcome = "failed"
        else:
            outcome, message = "passed", None

        results.append(TestResult(full_name, outcome, duration_ms, message))

    return RunSummary(
        total=len(results),
        passed=sum(1 for r in results if r.outcome == "passed"),
        failed=sum(1 for r in results if r.outcome == "failed"),
        skipped=sum(1 for r in results if r.outcome == "skipped"),
        duration_seconds=float(suite.get("time", "0")),
        results=results,
    )


def _tables_exist(cur) -> bool:
    """True when both tables resolve on the current search_path."""
    cur.execute(
        "SELECT to_regclass(%s) IS NOT NULL AND to_regclass(%s) IS NOT NULL",
        TABLES,
    )
    return bool(cur.fetchone()[0])


def log_run(conn_str: str, *, workflow: str, job: str, branch: str | None,
            sha: str, run_url: str | None, summary: RunSummary,
            schema: str | None = None) -> int:
    import psycopg
    from psycopg import sql

    with psycopg.connect(
        conn_str, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS
    ) as conn, conn.cursor() as cur:
        if schema:
            # Identifier() quotes it: `Travelplanner_schema` is mixed case, and
            # unquoted Postgres would fold it to lowercase and miss it. Only
            # this schema, not `public` as well, so first-use CREATEs land
            # here rather than wherever the server default points.
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        if not _tables_exist(cur):
            cur.execute(DDL)
        # One transaction for both inserts, so a failure part-way never leaves
        # a run row claiming N results with fewer (or none) behind it.
        with conn.transaction():
            cur.execute(
                """INSERT INTO ci_test_runs
                   (workflow, job, branch, sha, run_url, total, passed, failed, skipped, duration_seconds)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (workflow, job, branch, sha, run_url, summary.total, summary.passed,
                 summary.failed, summary.skipped, summary.duration_seconds),
            )
            run_id = cur.fetchone()[0]
            if summary.results:
                cur.executemany(
                    """INSERT INTO ci_test_results (run_id, test_name, outcome, duration_ms, failure_message)
                       VALUES (%s,%s,%s,%s,%s)""",
                    [(run_id, r.name, r.outcome, r.duration_ms, r.failure_message)
                     for r in summary.results],
                )
    return run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="Path to a JUnit XML report (pytest --junitxml)")
    parser.add_argument("--job", required=True, help="CI job name, e.g. 'tests' or 'e2e'")
    args = parser.parse_args(argv)

    conn_str = os.getenv("TEST_RESULTS_DATABASE_URL")
    if not conn_str:
        print("TEST_RESULTS_DATABASE_URL not set — skipping test-result logging "
              "(this never fails the build).")
        return 0

    try:
        summary = parse_junit_xml(args.report)
        run_id = log_run(
            conn_str,
            workflow=os.getenv("GITHUB_WORKFLOW", "local"),
            job=args.job,
            # GITHUB_REF_NAME is "22/merge" on a pull request; the head branch
            # is the name a person would actually search history for.
            branch=os.getenv("GITHUB_HEAD_REF") or os.getenv("GITHUB_REF_NAME"),
            sha=os.getenv("GITHUB_SHA", "unknown"),
            run_url=(
                f"{os.getenv('GITHUB_SERVER_URL')}/{os.getenv('GITHUB_REPOSITORY')}"
                f"/actions/runs/{os.getenv('GITHUB_RUN_ID')}"
                if os.getenv("GITHUB_RUN_ID") else None
            ),
            summary=summary,
            schema=os.getenv("TEST_RESULTS_DATABASE_SCHEMA", "").strip() or None,
        )
        print(f"Logged run {run_id} ({args.job}): "
              f"{summary.passed}/{summary.total} passed, {summary.failed} failed, "
              f"{summary.skipped} skipped.")
    except Exception as exc:  # noqa: BLE001 - logging must never fail the build
        print(f"::warning::Test-result logging failed, build unaffected: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
