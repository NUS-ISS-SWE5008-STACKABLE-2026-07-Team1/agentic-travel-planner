"""The parts of the migration that are testable without a Postgres server."""

import sqlite3

from flaskapp.database import SCHEMA_SQLITE
from scripts.migrate_sqlite_to_postgres import IDENTITY_TABLES, TABLES, copy_table


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
