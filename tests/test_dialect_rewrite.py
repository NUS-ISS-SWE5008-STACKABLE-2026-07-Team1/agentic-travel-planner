"""The query rewrite that lets one SQL string run on both engines.

Lives apart from test_schema_parity.py because it tests the connection layer's
behaviour, not the shape of the two schema constants; and apart from
test_database_portability.py, which is a deliberately hands-off source-text
guard. No live Postgres is needed: to_postgres_sql is a pure function, and
_Connection.execute is driven here by a recording stand-in for psycopg.
"""

from flaskapp.database import _NOW, _Connection, to_postgres_sql


class FakeCursor:
    def __init__(self):
        self.sql = None
        self.parameters = None

    def execute(self, sql, parameters):
        self.sql, self.parameters = sql, parameters


class FakeConnection:
    """The methods the shim touches on a psycopg connection."""

    def __init__(self):
        self.cursors = []
        self.scripts = []

    def cursor(self):
        self.cursors.append(FakeCursor())
        return self.cursors[-1]

    def execute(self, sql):
        # psycopg's connection-level execute, used only by executescript.
        self.scripts.append(sql)


def test_current_timestamp_becomes_the_schema_default_expression():
    """A bare keyword would raise: Postgres has no timestamptz -> TEXT cast."""
    rewritten = to_postgres_sql(
        "UPDATE agent_runs SET completed_at = CURRENT_TIMESTAMP WHERE id = ?"
    )
    assert "CURRENT_TIMESTAMP" not in rewritten
    assert _NOW in rewritten
    assert "to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')" in rewritten


def test_every_current_timestamp_in_a_query_is_rewritten():
    sql = """INSERT INTO travel_plans VALUES (?, ?, CURRENT_TIMESTAMP)
             ON CONFLICT (request_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP"""
    assert to_postgres_sql(sql).count(_NOW) == 2
    assert "CURRENT_TIMESTAMP" not in to_postgres_sql(sql)


def test_placeholders_become_psycopg_placeholders():
    assert to_postgres_sql("SELECT ? WHERE x = ?") == "SELECT %s WHERE x = %s"


def test_the_timestamp_expression_introduces_no_parameter_placeholder():
    """A % in the replacement text would be read by psycopg as a placeholder."""
    assert "%" not in _NOW
    rewritten = to_postgres_sql("UPDATE t SET a = CURRENT_TIMESTAMP WHERE b = ?")
    assert rewritten.count("%") == 1
    assert rewritten.count("%s") == 1


def test_the_two_substitutions_are_order_independent():
    """Neither substitution's output contains the other's input."""
    sql = "UPDATE t SET a = CURRENT_TIMESTAMP WHERE b = ? AND c = ?"
    other_order = sql.replace("?", "%s").replace("CURRENT_TIMESTAMP", _NOW)
    assert to_postgres_sql(sql) == other_order


def test_a_query_with_neither_token_is_returned_unchanged():
    sql = "SELECT COUNT(*) AS total FROM users"
    assert to_postgres_sql(sql) == sql


def test_postgres_connection_execute_applies_the_rewrite():
    raw = FakeConnection()
    connection = _Connection(raw, "postgres")
    connection.execute(
        "UPDATE planning_jobs SET completed_at = CURRENT_TIMESTAMP WHERE request_id = ?",
        ("abc",),
    )
    executed = raw.cursors[-1].sql
    assert "CURRENT_TIMESTAMP" not in executed
    assert _NOW in executed
    assert executed.endswith("WHERE request_id = %s")
    assert raw.cursors[-1].parameters == ("abc",)


def test_postgres_insert_returning_id_also_rewrites():
    class ReturningCursor(FakeCursor):
        def fetchone(self):
            return {"id": 7}

    class ReturningConnection(FakeConnection):
        def cursor(self):
            self.cursors.append(ReturningCursor())
            return self.cursors[-1]

    raw = ReturningConnection()
    connection = _Connection(raw, "postgres")
    new_id = connection.insert_returning_id(
        "INSERT INTO agent_runs (request_id, started_at) VALUES (?, CURRENT_TIMESTAMP)",
        ("abc",),
    )
    assert new_id == 7
    executed = raw.cursors[-1].sql
    assert "CURRENT_TIMESTAMP" not in executed
    assert executed.endswith("RETURNING id")


def test_sqlite_connection_executes_the_query_verbatim():
    """SQLite understands both tokens natively; the rewrite must not fire."""
    recorded = []

    class RawSqlite:
        def execute(self, sql, parameters):
            recorded.append((sql, parameters))

    sql = "UPDATE planning_jobs SET completed_at = CURRENT_TIMESTAMP WHERE request_id = ?"
    _Connection(RawSqlite(), "sqlite").execute(sql, ("abc",))
    assert recorded == [(sql, ("abc",))]


def test_json_extract_becomes_the_json_arrow_operator():
    """Postgres has no json_extract; ->> is the equivalent that returns text."""
    rewritten = to_postgres_sql(
        "SELECT json_extract(j.request_json, '$.origin') AS origin FROM planning_jobs j"
    )
    assert "json_extract" not in rewritten.lower()
    assert "(j.request_json)::json->>'origin'" in rewritten


def test_every_json_extract_in_one_query_is_rewritten():
    """get_recent_feedback calls it four times in a single SELECT."""
    sql = """SELECT json_extract(j.request_json, '$.origin') AS origin,
                    json_extract(j.request_json, '$.destination') AS destination,
                    json_extract(j.request_json, '$.origin_city') AS origin_city,
                    json_extract(j.request_json, '$.destination_city') AS destination_city
             FROM planning_jobs j LIMIT ?"""
    rewritten = to_postgres_sql(sql)
    assert "json_extract" not in rewritten.lower()
    assert rewritten.count("::json->>") == 4
    assert rewritten.endswith("LIMIT %s")


def test_json_extract_rewrite_introduces_no_binding_characters():
    """A % or ? in the replacement would be read as a parameter placeholder."""
    rewritten = to_postgres_sql(
        "SELECT json_extract(j.request_json, '$.origin') FROM planning_jobs j"
    )
    assert "%" not in rewritten
    assert "?" not in rewritten


def test_json_extract_rewrite_survives_a_nested_expression():
    """The capture stops at the '$. anchor, so a parenthesised argument works."""
    rewritten = to_postgres_sql("SELECT json_extract(COALESCE(a, b), '$.origin')")
    assert rewritten == "SELECT (COALESCE(a, b))::json->>'origin'"


def test_postgres_executescript_is_never_rewritten():
    """Schemas reach executescript already dialect-correct.

    SCHEMA_POSTGRES's own to_char defaults and DEFAULT '{}' bodies must arrive
    byte for byte: a second pass of to_postgres_sql over them would be a
    double rewrite.
    """
    raw = FakeConnection()
    script = (
        "CREATE TABLE t (created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "note TEXT DEFAULT 'is it? yes');"
    )
    _Connection(raw, "postgres").executescript(script)
    assert raw.scripts == [script]
    assert raw.cursors == []  # never went through the execute() path


def test_sqlite_executescript_is_never_rewritten():
    recorded = []

    class RawSqlite:
        def executescript(self, script):
            recorded.append(script)

    script = "CREATE TABLE t (created_at TEXT DEFAULT CURRENT_TIMESTAMP);"
    _Connection(RawSqlite(), "sqlite").executescript(script)
    assert recorded == [script]
