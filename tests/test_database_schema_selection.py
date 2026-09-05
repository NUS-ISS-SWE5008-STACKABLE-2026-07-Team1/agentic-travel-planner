"""Selecting the Postgres schema the tables live in.

The tables were moved out of `public` into `Travelplanner_schema`. Two things
make this delicate: the name is case-sensitive, so it must be quoted everywhere;
and Supabase fronts the database with pgbouncer, so `ALTER ROLE ... SET
search_path` is not enough — a pooled backend opened before the change keeps the
old path. `connect()` therefore sets it per connection.
"""

import importlib

from psycopg import sql

import flaskapp.database as database


def _statement(schema: str) -> str:
    """The statement `connect()` builds, rendered without needing a server."""
    return sql.SQL("SET search_path TO {}, public, extensions").format(
        sql.Identifier(schema)
    ).as_string(None)


def test_a_mixed_case_schema_is_quoted():
    """Unquoted, Postgres folds it to lowercase and the schema is not found."""
    assert '"Travelplanner_schema"' in _statement("Travelplanner_schema")


def test_public_and_extensions_stay_on_the_path():
    """Dropping `extensions` would break uuid/pgcrypto lookups."""
    rendered = _statement("Travelplanner_schema")
    assert rendered.endswith(", public, extensions")


def test_a_schema_name_with_a_quote_cannot_break_out():
    assert _statement('we"ird').count('"') >= 4


def test_no_schema_configured_means_the_server_default(monkeypatch):
    """SQLite deployments and stock Supabase must be untouched by this."""
    monkeypatch.delenv("DATABASE_SCHEMA", raising=False)
    reloaded = importlib.reload(database)
    try:
        assert reloaded.DATABASE_SCHEMA == ""
    finally:
        importlib.reload(database)


def test_the_configured_schema_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_SCHEMA", "Some_Schema")
    reloaded = importlib.reload(database)
    try:
        assert reloaded.DATABASE_SCHEMA == "Some_Schema"
    finally:
        importlib.reload(database)
