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
    """The two methods _Connection.execute touches on a psycopg connection."""

    def __init__(self):
        self.cursors = []

    def cursor(self):
        self.cursors.append(FakeCursor())
        return self.cursors[-1]


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
