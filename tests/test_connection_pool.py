"""Postgres connections are borrowed from a per-process pool, not opened per call.

The unit tests run everywhere. The ones marked `postgres` need TEST_DATABASE_URL,
which CI sets, and prove the properties that matter on the real server: the
same backend is reused, concurrency never exceeds the pool size, and a broken
transaction handed back does not poison the next borrower.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import pytest

from flaskapp import database

DSN = os.getenv("TEST_DATABASE_URL", "")
postgres = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")


class _FakePool:
    def __init__(self):
        self.lent = 0

    def getconn(self):
        self.lent += 1
        return object()


def test_a_postgres_dsn_borrows_from_the_pool(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(database, "_postgres_pool", lambda dsn: pool)
    monkeypatch.setattr(database, "DATABASE_POOL_SIZE", 5)

    connection = database.connect("postgresql://user@host/db")

    assert pool.lent == 1 and connection.dialect == "postgres"


def test_pool_size_zero_opens_a_connection_per_call(monkeypatch):
    """The one-variable way back, if pooling ever misbehaves in production."""
    import psycopg

    opened = []
    monkeypatch.setattr(database, "DATABASE_POOL_SIZE", 0)
    monkeypatch.setattr(database, "_postgres_pool",
                        lambda dsn: pytest.fail("the pool must not be used"))
    monkeypatch.setattr(database, "_configure_postgres", lambda raw: None)
    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kw: opened.append(kw) or object())

    database.connect("postgresql://user@host/db")

    assert opened and opened[0]["application_name"] == database.APPLICATION_NAME


def test_sqlite_never_touches_the_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_postgres_pool",
                        lambda dsn: pytest.fail("SQLite must not be pooled"))
    with database.connect(tmp_path / "x.sqlite3") as db:
        assert db.execute("SELECT 1 AS one").fetchone()["one"] == 1


def test_login_holds_no_connection_during_the_slow_password_check(tmp_path, monkeypatch):
    """The deadlock this guards against: a request holding a pooled connection
    while it waits (here, for scrypt) and then asking for a second one. Every
    auth helper takes and returns its own connection, and authenticate_user
    returns it before the hash check."""
    from flask import Flask

    open_now = []
    real_connect = database.connect

    @contextmanager
    def counting(target):
        with real_connect(target) as db:
            open_now.append(1)
            try:
                yield db
            finally:
                open_now.pop()

    path = tmp_path / "auth.sqlite3"
    database.initialize(path)
    database.seed_login_user(path, "someone@example.com", "hash")
    monkeypatch.setattr(database, "connect", counting)

    seen = []
    monkeypatch.setattr(database, "check_password_hash",
                        lambda stored, given: seen.append(len(open_now)) or False)
    app = Flask(__name__)
    app.config["DATABASE"] = path
    with app.app_context():
        assert database.authenticate_user("someone@example.com", "wrong") is None

    assert seen == [0], "a connection was held during the password hash check"
    assert open_now == []


# --- against a real Postgres (CI) ---------------------------------------------

@pytest.fixture
def pooled(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_POOL_SIZE", 3)
    database.initialize(DSN)
    pool = database._postgres_pool(DSN)
    yield pool


@postgres
def test_server_connections_are_reused(pooled):
    """Not "always the same one": the pool lends idle connections in rotation,
    so if earlier tests left two idle, calls alternate between them (seen in
    CI). The property is that calls stop opening connections."""
    pids = set()
    for _ in range(10):
        with database.connect(DSN) as db:
            pids.add(db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
    assert len(pids) <= pooled.max_size, f"10 calls used {len(pids)} server connections"
    assert len(pids) < 10, "every call opened its own connection"


@postgres
def test_concurrent_callers_never_exceed_the_pool(pooled):
    barrier = threading.Barrier(12)
    peak = []

    def work():
        barrier.wait()
        with database.connect(DSN) as db:
            peak.append(db.execute(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name = ?",
                (database.APPLICATION_NAME,),
            ).fetchone()["n"])

    threads = [threading.Thread(target=work) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    assert len(peak) == 12, "a caller never got a connection"
    assert max(peak) <= pooled.max_size


@postgres
def test_a_failed_transaction_does_not_poison_the_next_borrower(pooled):
    import psycopg

    with pytest.raises(psycopg.Error):
        with database.connect(DSN) as db:
            db.execute("SELECT * FROM no_such_table")
    with database.connect(DSN) as db:
        assert db.execute("SELECT 1 AS one").fetchone()["one"] == 1


@postgres
def test_uncommitted_work_handed_back_is_discarded(pooled):
    """close() without commit, as a caller that bails out would."""
    connection = database.connect(DSN)
    connection.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)", ("pool-ghost@example.com", "h")
    )
    connection.close()
    with database.connect(DSN) as db:
        row = db.execute(
            "SELECT count(*) AS n FROM users WHERE email = ?", ("pool-ghost@example.com",)
        ).fetchone()
    assert row["n"] == 0
