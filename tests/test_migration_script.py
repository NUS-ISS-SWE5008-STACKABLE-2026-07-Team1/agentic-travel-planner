"""The parts of the migration that are testable without a Postgres server."""

import re
import sqlite3

import pytest

from flaskapp.database import SCHEMA_SQLITE
from scripts.migrate_sqlite_to_postgres import (
    IDENTITY_TABLES,
    TABLE_PRIMARY_KEYS,
    TABLES,
    SchemaDriftError,
    blocking_tables,
    check_column_drift,
    copy_table,
    missing_primary_keys,
    reset_sequences,
)


def _fresh_sqlite(path):
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_SQLITE)
    connection.commit()
    connection.row_factory = sqlite3.Row
    return connection


def test_tables_are_listed_in_foreign_key_order():
    assert TABLES.index("users") < TABLES.index("travel_requests")
    assert TABLES.index("travel_requests") < TABLES.index("travel_plans")
    assert TABLES.index("agent_findings") < TABLES.index("options")
    assert TABLES.index("planning_jobs") < TABLES.index("agent_runs")
    assert TABLES.index("planning_jobs") < TABLES.index("plan_feedback")


def test_every_table_in_the_schema_is_migrated():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_SQLITE)
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == set(TABLES), "a table would be silently left behind"


def test_identity_tables_are_the_ones_with_generated_keys():
    assert IDENTITY_TABLES == (
        "users", "agent_findings", "options", "audit_events",
        "intake_messages", "agent_runs", "plan_feedback",
        "risk_standing_facts", "risk_seasonal_windows", "risk_dated_events",
        "flight_agent_eval_runs",
    )


def test_copy_table_is_idempotent(tmp_path):
    """Re-running must not duplicate rows. Verified SQLite-to-SQLite."""
    source_path, destination_path = tmp_path / "a.sqlite3", tmp_path / "b.sqlite3"
    for path in (source_path, destination_path):
        connection = sqlite3.connect(path)
        connection.executescript(SCHEMA_SQLITE)
        connection.commit()
        connection.close()
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    source.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (1, 'a@b.c', 'h')"
    )
    source.commit()
    destination = sqlite3.connect(destination_path)
    destination.row_factory = sqlite3.Row

    assert copy_table(source, destination, "users") == (1, 1)
    assert copy_table(source, destination, "users") == (1, 0)
    count = destination.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    assert count == 1


def test_table_primary_keys_cover_every_table():
    assert set(TABLE_PRIMARY_KEYS) == set(TABLES)


def test_table_primary_keys_do_not_assume_id_everywhere():
    """Three tables are keyed on something other than a generated id column;
    getting these wrong would make missing_primary_keys check the wrong
    column and either miss real gaps or report phantom ones."""
    assert TABLE_PRIMARY_KEYS == {
        "users": "id",
        "travel_requests": "id",
        "travel_plans": "request_id",
        "agent_findings": "id",
        "options": "id",
        "a2a_messages": "message_id",
        "audit_events": "id",
        "planning_jobs": "request_id",
        "intake_messages": "id",
        "agent_runs": "id",
        "plan_feedback": "id",
        "risk_standing_facts": "id",
        "risk_seasonal_windows": "id",
        "risk_dated_events": "id",
        "flight_agent_eval_runs": "id",
    }


def test_blocking_tables_flags_a_destination_that_already_has_rows(tmp_path):
    """A non-empty destination must abort: ON CONFLICT DO NOTHING has no
    conflict target, so a colliding row would silently keep the
    destination's copy and drop the source's with no error raised."""
    destination = _fresh_sqlite(tmp_path / "b.sqlite3")
    destination.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (1, 'a@b.c', 'h')"
    )
    destination.commit()

    assert blocking_tables(destination, TABLES, allow_nonempty=False) == ["users"]


def test_blocking_tables_is_empty_for_a_fresh_destination(tmp_path):
    destination = _fresh_sqlite(tmp_path / "b.sqlite3")

    assert blocking_tables(destination, TABLES, allow_nonempty=False) == []


def test_allow_nonempty_bypasses_the_check(tmp_path):
    """--allow-nonempty must let a deliberate resume proceed, even against
    the exact destination the check above refuses."""
    destination = _fresh_sqlite(tmp_path / "b.sqlite3")
    destination.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (1, 'a@b.c', 'h')"
    )
    destination.commit()

    assert blocking_tables(destination, TABLES, allow_nonempty=True) == []


def test_missing_primary_keys_is_empty_after_a_clean_copy(tmp_path):
    source = _fresh_sqlite(tmp_path / "a.sqlite3")
    destination = _fresh_sqlite(tmp_path / "b.sqlite3")
    source.execute("INSERT INTO users (id, email, password_hash) VALUES (1, 'a@b.c', 'h')")
    source.execute("INSERT INTO users (id, email, password_hash) VALUES (2, 'c@d.e', 'h')")
    source.commit()

    copy_table(source, destination, "users")

    assert missing_primary_keys(source, destination, "users") == set()


def test_missing_primary_keys_catches_a_row_a_count_check_would_miss(tmp_path):
    """The PK-set verification must fail when a row is missing from the
    destination — including the case a plain count comparison is fooled by:
    an unrelated pre-existing row inflates the destination's count enough
    that `existing >= read` still holds even though a specific source row
    never landed."""
    source = _fresh_sqlite(tmp_path / "a.sqlite3")
    destination = _fresh_sqlite(tmp_path / "b.sqlite3")
    source.execute("INSERT INTO users (id, email, password_hash) VALUES (1, 'a@b.c', 'h')")
    source.execute("INSERT INTO users (id, email, password_hash) VALUES (2, 'c@d.e', 'h')")
    source.commit()
    # id 1 copied correctly; id 2 never lands (simulating a dropped row).
    # id 3 is an unrelated row already in the destination, standing in for
    # what a previous partial run (or --allow-nonempty) could leave behind.
    destination.execute("INSERT INTO users (id, email, password_hash) VALUES (1, 'a@b.c', 'h')")
    destination.execute("INSERT INTO users (id, email, password_hash) VALUES (3, 'z@z.z', 'h')")
    destination.commit()

    read = len(source.execute("SELECT * FROM users").fetchall())
    existing = destination.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    assert existing >= read, "a plain count comparison would have called this a success"

    assert missing_primary_keys(source, destination, "users") == {2}


def test_missing_primary_keys_uses_request_id_for_travel_plans(tmp_path):
    """Confirms the PK lookup is not hard-coded to "id": travel_plans is
    keyed on request_id."""
    source = _fresh_sqlite(tmp_path / "a.sqlite3")
    destination = _fresh_sqlite(tmp_path / "b.sqlite3")
    request_columns = (
        "id, origin, destination, departure_date, return_date, travellers, "
        "currency, risk_tolerance"
    )
    request_values = "'req-1', 'SG', 'JP', '2026-09-01', '2026-09-10', 2, 'SGD', 'low'"
    for connection in (source, destination):
        connection.execute(
            f"INSERT INTO travel_requests ({request_columns}) VALUES ({request_values})"
        )
        connection.commit()
    source.execute(
        "INSERT INTO travel_plans (request_id, title, summary, itinerary_json, "
        "rationale_json, safety_passed) VALUES ('req-1', 't', 's', '[]', '[]', 1)"
    )
    source.commit()
    # destination's travel_plans is left empty: request_id 'req-1' never lands.

    assert missing_primary_keys(source, destination, "travel_plans") == {"req-1"}


# --- BLOCKING 1: source-only columns (e.g. travel_requests.origin_place, ---
# --- left behind by a half-reverted feature SQLite cannot drop a column ---
# --- for) must not silently drop data or crash the whole run. -------------

_REQUEST_COLUMNS = (
    "id, origin, destination, departure_date, return_date, travellers, "
    "currency, risk_tolerance"
)
_REQUEST_VALUES = "'req-1', 'SG', 'JP', '2026-09-01', '2026-09-10', 2, 'SGD', 'low'"


def test_copy_table_skips_a_source_only_column_that_is_always_null(tmp_path):
    """A column the source has but the destination schema doesn't declare
    must not blow up the migration when it never holds real data -- this is
    exactly travel_requests.origin_place/destination_place against the real
    instance database."""
    source, destination = (
        _fresh_sqlite(tmp_path / "a.sqlite3"), _fresh_sqlite(tmp_path / "b.sqlite3"),
    )
    source.execute("ALTER TABLE travel_requests ADD COLUMN origin_place TEXT")
    source.execute(
        f"INSERT INTO travel_requests ({_REQUEST_COLUMNS}, origin_place) "
        f"VALUES ({_REQUEST_VALUES}, NULL)"
    )
    source.commit()

    read, written = copy_table(source, destination, "travel_requests")

    assert (read, written) == (1, 1)
    row = destination.execute(
        "SELECT * FROM travel_requests WHERE id = 'req-1'"
    ).fetchone()
    assert row is not None
    assert "origin_place" not in row.keys(), "destination has no such column"


def test_copy_table_aborts_when_a_source_only_column_holds_data(tmp_path):
    """The same column with a real value must abort loudly instead of
    dropping it -- silently losing data is worse than a loud refusal."""
    source, destination = (
        _fresh_sqlite(tmp_path / "a.sqlite3"), _fresh_sqlite(tmp_path / "b.sqlite3"),
    )
    source.execute("ALTER TABLE travel_requests ADD COLUMN origin_place TEXT")
    source.execute(
        f"INSERT INTO travel_requests ({_REQUEST_COLUMNS}, origin_place) "
        f"VALUES ({_REQUEST_VALUES}, 'Singapore')"
    )
    source.commit()

    with pytest.raises(SchemaDriftError, match="origin_place"):
        copy_table(source, destination, "travel_requests")

    # Refusing to copy means refusing entirely -- no partial row either.
    assert destination.execute("SELECT COUNT(*) AS n FROM travel_requests").fetchone()["n"] == 0


def test_check_column_drift_skips_the_check_when_the_destination_table_is_absent(tmp_path):
    """A brand-new destination (no schema applied yet, the state --dry-run
    hits on a first-ever run) has nothing to compare against. That must read
    as "not checkable yet", not as every source column being drift."""
    source = _fresh_sqlite(tmp_path / "a.sqlite3")
    destination = sqlite3.connect(tmp_path / "empty.sqlite3")
    destination.row_factory = sqlite3.Row

    columns, skipped = check_column_drift(source, destination, "users")

    assert skipped == []
    assert "email" in columns


# --- SHOULD FIX 4: reset_sequences must not let setval's NULL-on-failure ---
# --- behavior pass as silent success. setval/pg_get_serial_sequence are ---
# --- Postgres-only, so this is exercised with a minimal fake connection ---
# --- rather than a real database -- the same "no live server" spirit as ---
# --- every other test in this file, just without a SQLite stand-in since ---
# --- SQLite has no equivalent to fake honestly. -----------------------------

class _FakeCursor:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return {"new_value": self._value}


class _FakeSequenceConnection:
    """Stands in for the Postgres destination reset_sequences talks to.
    One table is rigged to report a NULL new_value, as
    pg_get_serial_sequence(...) does when it cannot resolve a sequence and
    setval(NULL, ...) then returns NULL rather than raising."""

    def __init__(self, tables_returning_null=()):
        self._null_tables = set(tables_returning_null)
        self.committed = False

    def execute(self, sql, parameters=()):
        table = re.search(r"pg_get_serial_sequence\('(\w+)'", sql).group(1)
        return _FakeCursor(None if table in self._null_tables else 42)

    def commit(self):
        self.committed = True


def test_reset_sequences_reports_a_table_whose_sequence_could_not_be_resolved():
    destination = _FakeSequenceConnection(tables_returning_null={"agent_findings"})

    failures = reset_sequences(destination)

    assert len(failures) == 1
    assert "agent_findings" in failures[0]
    assert "NULL" in failures[0]
    assert destination.committed, "the other tables' sequences must still be advanced"


def test_reset_sequences_reports_nothing_when_every_setval_succeeds():
    destination = _FakeSequenceConnection()

    assert reset_sequences(destination) == []
    assert destination.committed


def test_reset_sequences_reports_every_table_whose_sequence_could_not_be_resolved():
    destination = _FakeSequenceConnection(
        tables_returning_null={"agent_findings", "plan_feedback"}
    )

    failures = reset_sequences(destination)

    assert len(failures) == 2
    assert any("agent_findings" in failure for failure in failures)
    assert any("plan_feedback" in failure for failure in failures)
