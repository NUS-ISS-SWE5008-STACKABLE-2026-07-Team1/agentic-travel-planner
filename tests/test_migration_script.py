"""The parts of the migration that are testable without a Postgres server."""

import sqlite3

from flaskapp.database import SCHEMA_SQLITE
from scripts.migrate_sqlite_to_postgres import (
    IDENTITY_TABLES,
    TABLE_PRIMARY_KEYS,
    TABLES,
    blocking_tables,
    copy_table,
    missing_primary_keys,
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
