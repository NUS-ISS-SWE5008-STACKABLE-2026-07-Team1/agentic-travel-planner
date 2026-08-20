"""One-off copy of the local SQLite database into Postgres.

Idempotent: every insert is ON CONFLICT DO NOTHING, so a re-run after a partial
failure resumes rather than duplicating. Primary keys are copied explicitly so
foreign keys stay valid, which is also why the identity sequences must be reset
afterwards — without that, the first row the application writes collides with a
migrated id.

Refuses to run against a destination that already has rows, unless
--allow-nonempty is passed: ON CONFLICT DO NOTHING has no conflict target, so a
colliding primary key silently keeps the destination's row and drops the
source's, with no error raised — the wrong failure mode to discover after the
fact.

Also refuses to silently drop a column: a source table can carry columns no
schema constant declares (a half-reverted feature SQLite cannot drop a
column for), and every copy is checked against what the destination actually
has, not against SCHEMA_SQLITE/SCHEMA_POSTGRES. An all-NULL source-only
column is skipped with a printed note; one holding real data aborts the run
rather than dropping it. --dry-run runs the same check against the
destination (read-only — it opens a connection but writes nothing) so this
surfaces before anything is copied.

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


class SchemaDriftError(RuntimeError):
    """A source column the destination schema does not declare holds real
    data. Raised instead of silently dropping the column on INSERT."""


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

# The primary-key column per table. Most identity tables use "id", but three
# tables don't: travel_plans and planning_jobs are keyed on request_id (they
# hang off travel_requests / are keyed by the job's own request id), and
# a2a_messages is keyed on message_id. travel_requests itself uses "id" too,
# just a TEXT id rather than a generated integer. Spelled out explicitly
# rather than assumed, so verification reads the column that is actually
# unique for each table instead of guessing "id" everywhere.
TABLE_PRIMARY_KEYS = {
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


def destination_table_exists(destination, table: str) -> bool:
    """Whether `table` exists yet in the destination.

    False for a brand-new Postgres database before `initialize()` has run —
    the common state a `--dry-run` hits on a first-ever migration. In that
    case there is nothing yet to compare columns against, so the caller
    treats it as "not checkable yet" rather than as drift.
    """
    dialect = getattr(destination, "dialect", "sqlite")
    if dialect == "postgres":
        row = destination.execute(
            "SELECT 1 AS present FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ?", (table,),
        ).fetchone()
    else:
        row = destination.execute(
            "SELECT 1 AS present FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    return row is not None


def destination_columns(destination, table: str) -> set[str]:
    """The destination table's actual columns, read from its own catalog
    (information_schema for Postgres, PRAGMA table_info for the SQLite
    destination the tests use) rather than trusted from
    SCHEMA_POSTGRES/SCHEMA_SQLITE, so this reflects a live database even if
    its DDL has drifted from what those constants declare.
    """
    dialect = getattr(destination, "dialect", "sqlite")
    if dialect == "postgres":
        rows = destination.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ?", (table,),
        ).fetchall()
        return {row["column_name"] for row in rows}
    rows = destination.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def check_column_drift(source, destination, table: str) -> tuple[list[str], list[str]]:
    """Decide which of `table`'s source columns are safe to copy.

    Returns (columns_to_copy, skipped_columns): the source/destination
    intersection, in source column order, and any source-only columns
    dropped because the destination has no place for them (e.g.
    travel_requests.origin_place/destination_place, left behind by a
    half-reverted feature — SQLite cannot drop a column, so they linger,
    always NULL, in the source file).

    Raises SchemaDriftError instead of dropping a column silently if it
    holds any non-NULL value in the source: that would be real data loss,
    not a harmless gap, and deserves a loud abort naming the column and how
    many rows are affected.
    """
    source_columns = [
        row["name"] for row in source.execute(f"PRAGMA table_info({table})").fetchall()
    ]
    if not destination_table_exists(destination, table):
        # Nothing to compare against yet; initialize() creates the table
        # before the real copy runs. Copy every source column and let a
        # genuine "relation does not exist" surface on its own — an
        # ordinary, loud error, not silent data loss.
        return source_columns, []
    dest_columns = destination_columns(destination, table)
    extra = [column for column in source_columns if column not in dest_columns]
    if extra:
        total = source.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for column in extra:
            populated = source.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {column} IS NOT NULL"
            ).fetchone()["n"]
            if populated:
                raise SchemaDriftError(
                    f"{table}.{column} exists in the source but not in the "
                    f"destination schema, and holds a non-NULL value in "
                    f"{populated} of {total} row(s). Refusing to drop real "
                    "data silently — resolve the schema drift (add the "
                    "column to the destination, or confirm dropping it is "
                    "safe) before re-running."
                )
    columns = [column for column in source_columns if column in dest_columns]
    return columns, extra


def copy_table(source, destination, table: str) -> tuple[int, int]:
    """Copy one table. Returns (rows read, rows written)."""
    rows = source.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0, 0
    columns, skipped = check_column_drift(source, destination, table)
    if skipped:
        print(
            f"{table}: source-only column(s) {', '.join(skipped)} are all "
            "NULL and not in the destination schema; skipping them."
        )
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


def blocking_tables(destination, tables, allow_nonempty: bool) -> list[str]:
    """Destination tables that must stop the migration before it writes
    anything, or [] if it's safe (or explicitly allowed) to proceed.

    ON CONFLICT DO NOTHING has no conflict target, so it swallows *any*
    unique violation, not just "this exact row already migrated". Against a
    destination that already holds unrelated rows, a colliding primary key
    silently keeps the destination's copy and drops the source's row — no
    exception, no non-zero exit, `existing >= read` still holds. It is worse
    for users: a colliding CITEXT-unique email drops the source user
    entirely, and the very next travel_requests insert then dies on its
    now-dangling user_id foreign key, part-way through the run. Checking
    "is every target table empty" up front turns that silent-data-loss /
    late-crash pair into a loud refusal before a single row is written.

    --allow-nonempty is the explicit override for the one legitimate case
    this would otherwise block: resuming a migration that already got partway
    through and left some tables populated.
    """
    if allow_nonempty:
        return []
    return [
        table for table in tables
        if destination.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    ]


def missing_primary_keys(source, destination, table: str) -> set:
    """Source primary keys that never landed in the destination.

    A row-count comparison (read vs. existing) is fooled two ways: rows in
    the destination that came from somewhere other than this copy (a
    previous run, --allow-nonempty, manual inserts) inflate the destination
    count enough to mask a real shortfall, and a source table that is
    legitimately empty passes trivially either way. Comparing the actual
    primary-key sets catches both — it names exactly which source rows are
    absent, rather than inferring from a total that a shortfall happened.
    """
    pk = TABLE_PRIMARY_KEYS[table]
    source_keys = {row[pk] for row in source.execute(f"SELECT {pk} FROM {table}").fetchall()}
    destination_keys = {
        row[pk] for row in destination.execute(f"SELECT {pk} FROM {table}").fetchall()
    }
    return source_keys - destination_keys


def reset_sequences(destination) -> list[str]:
    """Advance each identity sequence past the ids we inserted explicitly.

    setval's third argument controls whether the *next* nextval() repeats the
    value just set (false) or advances past it (true). Using `true`
    unconditionally is wrong on an empty table: COALESCE falls back to 1, and
    `setval(seq, 1, true)` marks 1 as already consumed even though nothing
    was inserted, so the application's first insert gets id 2 and burns id 1
    for no reason. The fix is to make the flag track whether the table
    actually has rows: `is_called` should be true only when MAX(id) is a real
    value, and false when it falls back to the identity's start value.

    setval(NULL, ...) returns NULL rather than raising, so if
    pg_get_serial_sequence ever fails to resolve a table's owning sequence,
    the naive version of this function would leave that sequence stuck at 1
    — and every later application insert into that table would collide with
    a migrated id — while reporting nothing wrong. Each result is checked
    for that, and any failure is returned rather than swallowed, so main()
    can fold it into the run's failure report and exit non-zero.
    """
    failures = []
    for table in IDENTITY_TABLES:
        result = destination.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE(m.max_id, 1), m.max_id IS NOT NULL) AS new_value "
            f"FROM (SELECT MAX(id) AS max_id FROM {table}) AS m"
        ).fetchone()
        if result is None or result["new_value"] is None:
            failures.append(
                f"reset_sequences: setval for {table}.id returned NULL — "
                "pg_get_serial_sequence could not resolve its sequence, so "
                "it was NOT advanced. The next insert into this table risks "
                "colliding with a migrated id."
            )
    destination.commit()
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="instance/travel_planner.sqlite3")
    parser.add_argument("--destination", required=True)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Connect to source and destination and report source counts "
             "plus any source-only-column schema drift, without writing "
             "any rows.",
    )
    parser.add_argument(
        "--allow-nonempty", action="store_true",
        help="Proceed even though the destination already has rows in some "
             "target table (for resuming a partial migration). Without this, "
             "a non-empty table aborts the run before anything is written.",
    )
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
        # Reads only: connects to the destination to compare columns but
        # never writes a row. If the destination's schema does not exist
        # yet (a brand-new database), there is nothing to compare against
        # for that table and check_column_drift says so instead of raising.
        destination = connect(arguments.destination)
        try:
            drifted = False
            for table in TABLES:
                count = source.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                print(f"{count:6d}  {table}")
                try:
                    _columns, skipped = check_column_drift(source, destination, table)
                except SchemaDriftError as error:
                    drifted = True
                    print(f"  SCHEMA DRIFT: {error}")
                    continue
                if skipped:
                    print(
                        f"  {table}: source-only column(s) {', '.join(skipped)} "
                        "would be skipped (all NULL, not in the destination schema)."
                    )
        finally:
            destination.close()
            source.close()
        return 1 if drifted else 0

    initialize(arguments.destination)
    destination = connect(arguments.destination)
    try:
        occupied = blocking_tables(destination, TABLES, arguments.allow_nonempty)
        if occupied:
            print(
                "Destination already has rows in: " + ", ".join(occupied) + ".\n"
                "Refusing to migrate: ON CONFLICT DO NOTHING has no conflict "
                "target, so a colliding row would silently keep the "
                "destination's copy and drop the source's, with no error. "
                "Pass --allow-nonempty if this is a deliberate resume of a "
                "partial migration."
            )
            return 2

        failures = []
        reset_error: Exception | None = None
        try:
            for table in TABLES:
                try:
                    read, written = copy_table(source, destination, table)
                except SchemaDriftError as error:
                    # An anticipated, named failure mode (a source-only
                    # column really does hold data) — report it through the
                    # normal failures list and stop, rather than letting it
                    # fall through to the same raw-traceback path an
                    # unanticipated exception still takes below.
                    failures.append(str(error))
                    print(f"{table:24s} ABORTED: {error}")
                    break
                missing = missing_primary_keys(source, destination, table)
                existing = destination.execute(
                    f"SELECT COUNT(*) AS n FROM {table}"
                ).fetchone()["n"]
                print(f"{table:24s} read {read:5d}  written {written:5d}  now {existing:5d}")
                if missing:
                    failures.append(
                        f"{table}: {len(missing)} source row(s) never landed in "
                        f"the destination: {sorted(missing)[:5]}"
                    )
        finally:
            # Runs even if the loop above raised, so a mid-run crash still
            # leaves the sequences advanced past whatever was actually
            # committed rather than stuck at 1 for every identity table.
            # Caught locally rather than left to propagate so a failure here
            # reports alongside the copy failures instead of replacing —
            # and hiding — whatever exception the loop itself raised.
            try:
                failures.extend(reset_sequences(destination))
            except Exception as error:  # noqa: BLE001 - reported, never raised
                reset_error = error
    finally:
        destination.close()
        source.close()

    if reset_error is not None:
        failures.append(f"reset_sequences failed: {reset_error}")

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("\nMigration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
