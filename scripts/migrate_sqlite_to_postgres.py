"""One-off copy of the local SQLite database into Postgres.

Idempotent: every insert is ON CONFLICT DO NOTHING, so a re-run after a partial
failure resumes rather than duplicating. Primary keys are copied explicitly so
foreign keys stay valid, which is also why the identity sequences must be reset
afterwards — without that, the first row the application writes collides with a
migrated id.

    python scripts/migrate_sqlite_to_postgres.py \
        --source instance/travel_planner.sqlite3 \
        --destination "$DATABASE_URL"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flaskapp.database import connect, initialize, is_postgres  # noqa: E402

# Parents before children. options follows agent_findings; agent_runs and
# plan_feedback follow planning_jobs.
TABLES = (
    "users",
    "travel_requests",
    "travel_plans",
    "agent_findings",
    "options",
    "a2a_messages",
    "audit_events",
    "planning_jobs",
    "intake_messages",
    "agent_runs",
    "plan_feedback",
)

# Tables whose id is generated, and whose sequence therefore needs resetting.
IDENTITY_TABLES = (
    "users",
    "agent_findings",
    "options",
    "audit_events",
    "intake_messages",
    "agent_runs",
    "plan_feedback",
)


def copy_table(source, destination, table: str) -> tuple[int, int]:
    """Copy one table. Returns (rows read, rows written)."""
    rows = source.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0, 0
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    statement = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        "ON CONFLICT DO NOTHING"
    )
    written = 0
    for row in rows:
        cursor = destination.execute(statement, tuple(row[column] for column in columns))
        written += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    destination.commit()
    return len(rows), written


def reset_sequences(destination) -> None:
    """Advance each identity sequence past the ids we inserted explicitly.

    setval's third argument controls whether the *next* nextval() repeats the
    value just set (false) or advances past it (true). Using `true`
    unconditionally is wrong on an empty table: COALESCE falls back to 1, and
    `setval(seq, 1, true)` marks 1 as already consumed even though nothing
    was inserted, so the application's first insert gets id 2 and burns id 1
    for no reason. The fix is to make the flag track whether the table
    actually has rows: `is_called` should be true only when MAX(id) is a real
    value, and false when it falls back to the identity's start value.
    """
    for table in IDENTITY_TABLES:
        destination.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE(m.max_id, 1), m.max_id IS NOT NULL) "
            f"FROM (SELECT MAX(id) AS max_id FROM {table}) AS m"
        )
    destination.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="instance/travel_planner.sqlite3")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="Connect and report source counts without writing.")
    arguments = parser.parse_args(argv)

    if not is_postgres(arguments.destination):
        print(f"Destination is not a Postgres DSN: {arguments.destination}")
        return 2
    if not Path(arguments.source).is_file():
        print(f"No SQLite database at {arguments.source}")
        return 2

    source = sqlite3.connect(arguments.source)
    source.row_factory = sqlite3.Row

    if arguments.dry_run:
        for table in TABLES:
            count = source.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            print(f"{count:6d}  {table}")
        return 0

    initialize(arguments.destination)
    destination = connect(arguments.destination)
    failures = []
    try:
        for table in TABLES:
            read, written = copy_table(source, destination, table)
            existing = destination.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()["n"]
            print(f"{table:24s} read {read:5d}  written {written:5d}  now {existing:5d}")
            if existing < read:
                failures.append(f"{table}: {read} rows read but only {existing} present")
        reset_sequences(destination)
    finally:
        destination.close()
        source.close()

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("\nMigration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
