"""Only one process sets the schema up at a time (initialize()'s advisory lock).

Every pod runs initialize() on start, and its steps are "check, then change".
Two pods starting together can both find a column missing and both add it; the
second crashes with "already exists". The Postgres tests need TEST_DATABASE_URL
(CI sets it).
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from flaskapp import database

DSN = os.getenv("TEST_DATABASE_URL", "")
postgres = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")


def test_the_lock_is_scoped_to_the_transaction_and_the_schema():
    """xact: released by initialize()'s own commit/rollback, so a pod that dies
    mid-setup cannot leave it held. Schema: Render and GKE share one database
    in different schemas and must not queue behind each other."""
    sql = database.SCHEMA_SETUP_LOCK_SQL
    assert "pg_advisory_xact_lock" in sql and "current_schema()" in sql
    # Survives the dialect rewrite untouched (no ? or CURRENT_TIMESTAMP in it).
    assert database.to_postgres_sql(sql) == sql


def test_sqlite_does_not_take_it(tmp_path, monkeypatch):
    executed = []
    real = database._Connection.execute

    def spy(self, sql, parameters=()):
        executed.append(sql)
        return real(self, sql, parameters)

    monkeypatch.setattr(database._Connection, "execute", spy)
    database.initialize(tmp_path / "x.sqlite3")
    assert database.SCHEMA_SETUP_LOCK_SQL not in executed


@postgres
def test_setup_waits_while_another_process_holds_the_lock():
    """Deterministic: hold the lock elsewhere, and initialize() must wait."""
    import psycopg

    database.initialize(DSN)
    holder = psycopg.connect(DSN)
    holder.execute(database.SCHEMA_SETUP_LOCK_SQL)  # held until holder commits
    done = threading.Event()
    errors = []

    def setup():
        try:
            database.initialize(DSN)
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)
        done.set()

    worker = threading.Thread(target=setup)
    worker.start()
    try:
        assert not done.wait(1.5), "initialize() ran while another process held the lock"
    finally:
        holder.commit()  # releases the transaction-scoped lock
        holder.close()
    assert done.wait(30), "initialize() never resumed after the lock was released"
    worker.join(5)
    assert errors == []


@postgres
def test_simultaneous_setups_adding_a_missing_column_all_succeed():
    """The real race: a column every starting pod sees as missing."""
    database.initialize(DSN)
    with database.connect(DSN) as db:
        db.execute("ALTER TABLE planning_jobs DROP COLUMN IF EXISTS response_json")

    barrier = threading.Barrier(4)
    errors = []

    def pod_starts():
        barrier.wait()
        try:
            database.initialize(DSN)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=pod_starts) for _ in range(4)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    assert errors == [], errors
    with database.connect(DSN) as db:
        columns = {r["column_name"] for r in db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'planning_jobs'"
        ).fetchall()}
    assert "response_json" in columns
    assert time.monotonic() - started < 60
