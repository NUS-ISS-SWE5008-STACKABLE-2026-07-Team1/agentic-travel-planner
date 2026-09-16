"""`scripts/log_test_results.py`'s database half, against a real Postgres.

Skipped without TEST_DATABASE_URL, like `test_database_postgres.py`. CI sets
it (the `postgres:16` service in ci-checks.yml), and because this file's name
contains "postgres", the "Confirm the Postgres suite actually ran" step fails
the build if these tests are ever collected but skipped there.

Each test gets its own mixed-case schema, mirroring Supabase's
`Travelplanner_schema`, so quoting is exercised and nothing leaks into
`public` or into the app tables `test_database_postgres.py` truncates.
"""

from __future__ import annotations

import os
import uuid

import pytest

from scripts.log_test_results import log_run, parse_junit_xml

DSN = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")

JUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" errors="0" failures="1" skipped="1" tests="3" time="1.5">
<testcase classname="tests.test_example" name="test_pass" time="0.010"/>
<testcase classname="tests.test_example" name="test_fail" time="0.020">
<failure message="AssertionError: boom">AssertionError: boom</failure>
</testcase>
<testcase classname="tests.test_example" name="test_skip" time="0.000">
<skipped message="not applicable here" type="pytest.skip"/>
</testcase>
</testsuite>
</testsuites>
"""

RUN_FIELDS = dict(
    workflow="CI — Static & Functional Checks", job="tests", branch="release",
    sha="abc1234", run_url="https://example.invalid/runs/1",
)


@pytest.fixture()
def summary(tmp_path):
    path = tmp_path / "report.xml"
    path.write_text(JUNIT_XML, encoding="utf-8")
    return parse_junit_xml(str(path))


@pytest.fixture()
def schema():
    import psycopg
    from psycopg import sql

    name = f"Ci_Log_Test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    yield name
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def _tables_in(schema_name: str) -> set[str]:
    import psycopg

    with psycopg.connect(DSN) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (schema_name,),
        ).fetchall()
    return {row[0] for row in rows}


def test_logs_a_run_and_one_row_per_test(schema, summary):
    import psycopg
    from psycopg import sql

    run_id = log_run(DSN, summary=summary, schema=schema, **RUN_FIELDS)

    with psycopg.connect(DSN) as conn:
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        run = conn.execute(
            "SELECT job, branch, sha, total, passed, failed, skipped FROM ci_test_runs WHERE id = %s",
            (run_id,),
        ).fetchone()
        results = dict(conn.execute(
            "SELECT test_name, outcome FROM ci_test_results WHERE run_id = %s", (run_id,)
        ).fetchall())
        skip_reason = conn.execute(
            "SELECT failure_message FROM ci_test_results WHERE run_id = %s AND outcome = 'skipped'",
            (run_id,),
        ).fetchone()[0]

    assert run == ("tests", "release", "abc1234", 3, 1, 1, 1)
    assert results == {
        "tests.test_example::test_pass": "passed",
        "tests.test_example::test_fail": "failed",
        "tests.test_example::test_skip": "skipped",
    }
    assert skip_reason == "not applicable here"


def test_tables_land_in_the_configured_schema_not_public(schema, summary):
    log_run(DSN, summary=summary, schema=schema, **RUN_FIELDS)
    assert {"ci_test_runs", "ci_test_results"} <= _tables_in(schema)
    assert not {"ci_test_runs", "ci_test_results"} & _tables_in("public")


def test_a_second_run_reuses_the_tables(schema, summary):
    first = log_run(DSN, summary=summary, schema=schema, **RUN_FIELDS)
    second = log_run(DSN, summary=summary, schema=schema, **RUN_FIELDS)
    assert second > first


@pytest.fixture()
def insert_only_dsn(schema, summary):
    """A login that may insert into the two tables and do nothing else.

    The shape recommended in docs/handoff/ci-test-logging-supabase.md: the
    tables are created once by an owner, then the CI login gets USAGE on the
    schema and INSERT/SELECT on the tables — never CREATE.
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    log_run(DSN, summary=summary, schema=schema, **RUN_FIELDS)  # owner creates the tables

    role = f"ci_logger_{uuid.uuid4().hex[:8]}"
    password = uuid.uuid4().hex
    s = sql.Identifier(schema)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(s, sql.Identifier(role)))
        conn.execute(sql.SQL(
            "GRANT SELECT, INSERT ON {}.ci_test_runs, {}.ci_test_results TO {}"
        ).format(s, s, sql.Identifier(role)))
        conn.execute(sql.SQL(
            "GRANT USAGE ON SEQUENCE {}.ci_test_runs_id_seq, {}.ci_test_results_id_seq TO {}"
        ).format(s, s, sql.Identifier(role)))
    yield make_conninfo(DSN, user=role, password=password)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_an_insert_only_login_can_log_once_the_tables_exist(schema, summary, insert_only_dsn):
    assert log_run(insert_only_dsn, summary=summary, schema=schema, **RUN_FIELDS) > 0


def test_an_insert_only_login_really_cannot_create(schema, insert_only_dsn):
    """The control for the test above.

    Proves the role is genuinely limited — and that `CREATE TABLE IF NOT
    EXISTS` on tables that already exist is still refused for it, which is
    exactly why `log_run` checks for the tables before issuing any DDL.
    """
    import psycopg
    from psycopg import sql

    with psycopg.connect(insert_only_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE IF NOT EXISTS ci_test_runs (id BIGINT)")
