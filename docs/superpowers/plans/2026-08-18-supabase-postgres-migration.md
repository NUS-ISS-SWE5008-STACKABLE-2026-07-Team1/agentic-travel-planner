# Supabase Postgres Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the deployed application against hosted Supabase Postgres instead of an ephemeral SQLite file, without rewriting the 135 working SQL queries or changing any calling module.

**Architecture:** A thin dialect layer at the top of `flaskapp/database.py` returns either a `sqlite3` or a `psycopg` connection behind one small interface, translating `?` placeholders and generated-key retrieval. `Config.DATABASE` resolves to a DSN when `DATABASE_URL` is set and to the existing SQLite path otherwise, so local development and the 308 existing tests are unchanged by default. Four admin-dashboard queries are first made dialect-neutral so that no query needs a per-engine copy.

**Tech Stack:** Python 3.11, Flask, SQLite (local/tests), PostgreSQL 16 via Supabase (deployed), `psycopg[binary]` 3.2, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-18-supabase-postgres-migration-design.md`

## Global Constraints

- Python 3.11 or newer. `psycopg[binary]>=3.2,<4`.
- No calling module changes. The eleven modules importing `flaskapp.database` call functions, never SQL. Only `jobs.py` and `service.py` change, and only to stop coercing a DSN through `Path()`.
- No ORM. All existing SQL stays as written; only placeholders are translated.
- Both engines must store `created_at`/`submitted_at`/`started_at` as TEXT in exactly `YYYY-MM-DD HH:MM:SS`. The admin dashboard sorts and slices these as strings.
- Column *order* must match between the two schema constants: `save_plan` inserts into `a2a_messages` positionally with 11 unnamed values.
- No secret may enter the repository. `DATABASE_URL` lives in `.env.secrets` (gitignored) locally and in the Render dashboard as `sync: false`.
- Run `pytest -q` before every commit. All 308 existing tests must stay green throughout; no task may leave them red.

---

### Task 1: Stop building an app at import time

`flaskapp/__init__.py:32` runs `app = create_app()` when the package is imported, which calls `init_app` → `initialize()`. With SQLite that quietly creates a file. Once Task 4 honours `DATABASE_URL`, every test import would instead connect to Supabase and run DDL against production. This must land first.

Nothing depends on the export: no module imports `from flaskapp import app`, and `render.yaml` starts gunicorn with `flaskapp:create_app()`.

**Files:**
- Modify: `flaskapp/__init__.py:30-32`
- Test: `tests/test_app_factory.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `flaskapp` package importable with no side effects. `create_app(config_object=Config) -> Flask` is unchanged and remains the only entry point.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app_factory.py
"""Importing the package must not build an app or touch a database.

Once DATABASE_URL is honoured, an import-time create_app() would connect to
the deployed Postgres and run schema DDL against it — during tests, on every
developer machine, on every import.
"""

import importlib


def test_importing_the_package_does_not_build_an_app():
    flaskapp = importlib.import_module("flaskapp")
    assert not hasattr(flaskapp, "app"), (
        "flaskapp.app builds an application at import time; use create_app()"
    )


def test_create_app_is_still_exported():
    flaskapp = importlib.import_module("flaskapp")
    assert callable(flaskapp.create_app)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_factory.py -v`
Expected: `test_importing_the_package_does_not_build_an_app` FAILS on the assert; the second test passes.

- [ ] **Step 3: Delete the import-time instantiation**

Remove these three lines from the end of `flaskapp/__init__.py`:

```python
# Backwards-compatible export for code importing ``from flaskapp import app``.
app = create_app()
```

(Delete the blank line separating them from `return app` as well, leaving the file ending at `create_app`'s `return app`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app_factory.py -v && python -m pytest -q`
Expected: both new tests PASS, and the full suite is still green (308 passed).

- [ ] **Step 5: Commit**

```bash
git add flaskapp/__init__.py tests/test_app_factory.py
git commit -m "fix: do not build an app at package import time

Once DATABASE_URL is honoured this would connect every test import to the
deployed database and run DDL against it. Nothing imports the export and
gunicorn already uses create_app()."
```

---

### Task 2: Make the dashboard queries dialect-neutral

`get_admin_token_summary` and `get_platform_dashboard` use `DATE(...)` (15 uses) and `STRFTIME('%Y-%m', ...)` (6 uses). Neither exists in Postgres with these semantics, and the literal `%` collides with psycopg's own parameter binding. Because timestamps are stored in a fixed `YYYY-MM-DD HH:MM:SS` format, both are string slices that behave identically in SQLite and Postgres.

This task changes SQLite-only code while still on SQLite, so the existing dashboard tests prove the rewrite is behaviour-preserving before any Postgres exists.

**Files:**
- Modify: `flaskapp/database.py:562-568, 575-581, 629-655, 682-701`
- Test: `tests/test_database_portability.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `flaskapp/database.py` containing no `DATE(` or `STRFTIME` tokens. `get_admin_token_summary(path)` and `get_platform_dashboard(path)` keep their existing signatures and return shapes exactly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database_portability.py
"""Guards that keep database.py runnable on both engines.

A source-text guard is crude but it is the only check that fails at the moment
someone reintroduces an engine-specific function, rather than months later when
the deployed dashboard returns empty rows.
"""

from pathlib import Path

SOURCE = Path("flaskapp/database.py").read_text(encoding="utf-8")


def test_no_sqlite_only_date_functions():
    assert "STRFTIME" not in SOURCE.upper(), "use SUBSTR(x, 1, 7) for month grouping"
    assert "DATE(" not in SOURCE.upper().replace("UPDATE(", ""), (
        "use SUBSTR(x, 1, 10) for day grouping"
    )


def test_no_literal_percent_in_sql():
    """psycopg reads % as its own placeholder, so a literal % breaks binding."""
    assert "'%" not in SOURCE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_database_portability.py -v`
Expected: FAIL — both `STRFTIME` and `DATE(` are present.

- [ ] **Step 3: Replace the date functions**

Apply throughout `get_admin_token_summary` and `get_platform_dashboard`:

| Replace | With |
| --- | --- |
| `DATE(j.submitted_at)` | `SUBSTR(j.submitted_at, 1, 10)` |
| `DATE(submitted_at)` | `SUBSTR(submitted_at, 1, 10)` |
| `DATE(created_at)` | `SUBSTR(created_at, 1, 10)` |
| `STRFTIME('%Y-%m', j.submitted_at)` | `SUBSTR(j.submitted_at, 1, 7)` |
| `STRFTIME('%Y-%m', created_at)` | `SUBSTR(created_at, 1, 7)` |

Every occurrence changes, including those in `WHERE`, `GROUP BY` and `ORDER BY`. For example `flaskapp/database.py:575-581` becomes:

```python
        agent_trend_monthly = [dict(row) for row in db.execute(
            """SELECT SUBSTR(j.submitted_at, 1, 7) AS date, r.agent,
                      SUM(r.input_tokens) AS input_tokens,
                      SUM(r.output_tokens) AS output_tokens,
                      SUM(r.total_tokens) AS total_tokens
               FROM agent_runs r JOIN planning_jobs j ON j.request_id = r.request_id
               WHERE SUBSTR(j.submitted_at, 1, 10) >= ?
               GROUP BY SUBSTR(j.submitted_at, 1, 7), r.agent ORDER BY date, r.agent""",
            (month_start.isoformat(),),
        ).fetchall()]
```

The bound parameters are unchanged: `month_start.isoformat()` already produces `YYYY-MM-DD`, which compares correctly against a 10-character slice.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_database_portability.py tests/test_api.py tests/test_database.py -v && python -m pytest -q`
Expected: new guards PASS; the existing dashboard and admin tests still PASS unchanged (308 + 3 passed). If any dashboard test fails, the rewrite changed behaviour — fix the query, do not adjust the test.

- [ ] **Step 5: Commit**

```bash
git add flaskapp/database.py tests/test_database_portability.py
git commit -m "refactor: dialect-neutral date grouping in the admin dashboard

DATE() and STRFTIME() are SQLite-only and the literal % in STRFTIME collides
with psycopg parameter binding. Timestamps are stored in a fixed format, so
SUBSTR gives identical results on both engines with one query text."
```

---

### Task 3: Dialect layer and Postgres schema

**Files:**
- Modify: `flaskapp/database.py:1-17` (imports, schema constants), `176-233` (`connect`, `initialize`), `255-272` (`create_user`), `290-305` (`register_admin`), `317-373` (`save_plan`), `608, 656, 701` (positional row access)
- Modify: `tests/test_database.py:7, 14, 118` (`SCHEMA` → `SCHEMA_SQLITE`)
- Test: `tests/test_schema_parity.py` (create)

**Interfaces:**
- Consumes: Task 2's dialect-neutral queries.
- Produces:
  - `is_postgres(target: Path | str) -> bool`
  - `connect(target: Path | str) -> _Connection`
  - `_Connection.dialect` — `"sqlite"` or `"postgres"`
  - `_Connection.execute(sql: str, parameters: tuple = ()) -> cursor`
  - `_Connection.insert_returning_id(sql: str, parameters: tuple = ()) -> int`
  - `SCHEMA_SQLITE: str`, `SCHEMA_POSTGRES: str`
  - `IntegrityError` — the dialect-neutral duplicate-key exception tuple

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema_parity.py
"""The two schema constants must declare the same shape.

save_plan inserts into a2a_messages positionally with 11 unnamed values, so
column ORDER is load-bearing, not just column names.
"""

import re

from flaskapp.database import SCHEMA_POSTGRES, SCHEMA_SQLITE

TABLE = re.compile(
    r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.DOTALL
)


def columns(schema: str) -> dict[str, list[str]]:
    tables = {}
    for name, body in TABLE.findall(schema):
        names = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("--") or line.upper().startswith(
                ("PRIMARY KEY", "UNIQUE", "CHECK", "FOREIGN KEY")
            ):
                continue
            names.append(line.split()[0])
        tables[name] = names
    return tables


def test_both_schemas_declare_the_same_tables():
    assert set(columns(SCHEMA_SQLITE)) == set(columns(SCHEMA_POSTGRES))


def test_both_schemas_declare_the_same_columns_in_the_same_order():
    sqlite_tables, postgres_tables = columns(SCHEMA_SQLITE), columns(SCHEMA_POSTGRES)
    for table, names in sqlite_tables.items():
        assert names == postgres_tables[table], f"{table} column order differs"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schema_parity.py -v`
Expected: FAIL with `ImportError: cannot import name 'SCHEMA_POSTGRES'`.

- [ ] **Step 3a: Rename the existing schema constant**

In `flaskapp/database.py`, rename `SCHEMA = """..."""` to `SCHEMA_SQLITE = """..."""`, leaving its content untouched. Update the three references in `tests/test_database.py` (lines 7, 14, 118) from `SCHEMA` to `SCHEMA_SQLITE`.

- [ ] **Step 3b: Add the Postgres schema**

Add directly beneath `SCHEMA_SQLITE`. Differences from the SQLite text, and only these: `citext` extension enabled; `PRAGMA` line dropped; `INTEGER PRIMARY KEY AUTOINCREMENT` → `INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY` (BY DEFAULT, not ALWAYS, so the migration can insert explicit ids); `REAL` → `DOUBLE PRECISION`; `TEXT COLLATE NOCASE` → `CITEXT`; `DEFAULT CURRENT_TIMESTAMP` → `DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')`.

```python
_NOW = "to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')"

SCHEMA_POSTGRES = f"""
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    name TEXT,
    email CITEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    country TEXT,
    birthday TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT {_NOW}
);

CREATE TABLE IF NOT EXISTS travel_requests (
    id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    origin_city TEXT,
    destination_city TEXT,
    departure_date TEXT NOT NULL,
    return_date TEXT NOT NULL,
    travellers INTEGER NOT NULL CHECK (travellers BETWEEN 1 AND 20),
    traveller_ages_json TEXT NOT NULL DEFAULT '[]',
    traveller_genders_json TEXT NOT NULL DEFAULT '[]',
    traveller_accessibility_needs_json TEXT NOT NULL DEFAULT '[]',
    budget DOUBLE PRECISION,
    currency TEXT NOT NULL,
    preferences_json TEXT NOT NULL DEFAULT '[]',
    accessibility_needs_json TEXT NOT NULL DEFAULT '[]',
    refinement_notes_json TEXT NOT NULL DEFAULT '[]',
    risk_tolerance TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL DEFAULT {_NOW}
);

CREATE TABLE IF NOT EXISTS travel_plans (
    request_id TEXT PRIMARY KEY REFERENCES travel_requests(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    itinerary_json TEXT NOT NULL,
    estimated_total_cost DOUBLE PRECISION,
    currency TEXT,
    rationale_json TEXT NOT NULL,
    alternatives_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    limitations_json TEXT NOT NULL DEFAULT '[]',
    safety_passed INTEGER NOT NULL,
    safety_checks_json TEXT NOT NULL DEFAULT '[]',
    safety_warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT {_NOW}
);

CREATE TABLE IF NOT EXISTS agent_findings (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES travel_requests(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    summary TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS options (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES agent_findings(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    estimated_cost DOUBLE PRECISION,
    currency TEXT,
    source_urls_json TEXT NOT NULL DEFAULT '[]',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    limitations_json TEXT NOT NULL DEFAULT '[]',
    selection_factors_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS a2a_messages (
    message_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES travel_requests(id) ON DELETE CASCADE,
    protocol_version TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{{}}',
    error_json TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    request_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    agent TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{{}}',
    previous_hash TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS planning_jobs (
    request_id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT {_NOW},
    completed_at TEXT,
    error_type TEXT,
    session_status TEXT NOT NULL DEFAULT 'active',
    session_ended_at TEXT
);

CREATE TABLE IF NOT EXISTS intake_messages (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES planning_jobs(request_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT {_NOW}
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES planning_jobs(request_id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT {_NOW},
    completed_at TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    response_json TEXT,
    error_type TEXT,
    UNIQUE(request_id, agent)
);

CREATE TABLE IF NOT EXISTS plan_feedback (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE REFERENCES planning_jobs(request_id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT {_NOW},
    updated_at TEXT NOT NULL DEFAULT {_NOW}
);

CREATE INDEX IF NOT EXISTS idx_requests_user_created
    ON travel_requests(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_findings_request ON agent_findings(request_id);
CREATE INDEX IF NOT EXISTS idx_messages_request ON a2a_messages(request_id);
CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_events(request_id, id);
CREATE INDEX IF NOT EXISTS idx_jobs_submitted ON planning_jobs(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_intake_messages_request ON intake_messages(request_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_request ON agent_runs(request_id, agent);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON plan_feedback(created_at DESC);
"""
```

Note the doubled braces `'{{}}'` — this is an f-string, so a literal `{}` default must be escaped.

- [ ] **Step 4: Run the parity test**

Run: `python -m pytest tests/test_schema_parity.py -v`
Expected: PASS. A failure names the table whose columns differ — fix `SCHEMA_POSTGRES` to match, never the reverse.

- [ ] **Step 5: Add the connection layer**

Replace `connect()` in `flaskapp/database.py` and add above it:

```python
POSTGRES_SCHEMES = ("postgresql://", "postgres://")


def is_postgres(target: Path | str) -> bool:
    """True when the target names a Postgres DSN rather than a SQLite file."""
    return isinstance(target, str) and target.startswith(POSTGRES_SCHEMES)


class _Connection:
    """The slice of sqlite3.Connection this module actually uses.

    Deliberately small — execute, commit, rollback, close, and context-manager
    entry/exit. Do not widen it speculatively.

    Both dialects commit AND close on a clean context exit. sqlite3 alone would
    commit and leave the connection open; psycopg alone would close. Making
    them agree is what lets `with connect(...)` mean one thing in all 21 call
    sites.
    """

    def __init__(self, raw, dialect: str):
        self._raw = raw
        self.dialect = dialect

    def execute(self, sql: str, parameters: tuple = ()):
        if self.dialect == "postgres":
            cursor = self._raw.cursor()
            cursor.execute(sql.replace("?", "%s"), parameters)
            return cursor
        return self._raw.execute(sql, parameters)

    def executescript(self, script: str):
        if self.dialect == "postgres":
            self._raw.execute(script)
        else:
            self._raw.executescript(script)
        return self

    def insert_returning_id(self, sql: str, parameters: tuple = ()) -> int:
        """The generated primary key, however this engine surfaces it."""
        if self.dialect == "postgres":
            return self.execute(sql + " RETURNING id", parameters).fetchone()["id"]
        return self.execute(sql, parameters).lastrowid

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self._raw.close()
        return False


CONNECTION_HELP = (
    "Could not reach the Postgres server. If this is Supabase, check that "
    "DATABASE_URL is the SESSION POOLER string (its host contains "
    "pooler.supabase.com) and not the direct connection — the direct endpoint "
    "is IPv6-only and most hosts, including Render, cannot reach it. Also "
    "percent-encode any /, @, # or ? in the password."
)


def connect(target: Path | str) -> _Connection:
    """Open a connection to a SQLite path or a Postgres DSN."""
    if is_postgres(target):
        import psycopg
        from psycopg.rows import dict_row

        try:
            raw = psycopg.connect(target, row_factory=dict_row)
        except psycopg.OperationalError as error:
            # The failure is almost always the pooler/direct distinction, and
            # the driver's own message ("could not translate host name") does
            # not hint at it. Say so once, here, rather than in a runbook.
            raise psycopg.OperationalError(f"{error}\n\n{CONNECTION_HELP}") from error
        return _Connection(raw, "postgres")
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return _Connection(connection, "sqlite")
```

Add near the imports:

```python
# psycopg is imported lazily inside connect(), so a SQLite-only environment
# never needs it installed. This tuple is what `except` clauses catch.
IntegrityError: tuple[type[Exception], ...] = (sqlite3.IntegrityError,)
try:  # pragma: no cover - depends on the installed extras
    import psycopg

    IntegrityError = (sqlite3.IntegrityError, psycopg.errors.UniqueViolation)
except ImportError:
    pass
```

- [ ] **Step 6: Update the dialect-specific call sites**

1. `create_user` — change `except sqlite3.IntegrityError:` to `except IntegrityError:`, and replace the insert + `cursor.lastrowid` pair with `user_id = db.insert_returning_id(...)`, then select on `user_id`.
2. `register_admin` — replace `cursor = db.execute(...)` / `user_id = cursor.lastrowid` with `user_id = db.insert_returning_id(...)`.
3. `save_plan` — the `agent_findings` insert feeding `options` becomes `finding_id = db.insert_returning_id(...)`, and the `options` insert uses `finding_id` instead of `cursor.lastrowid`.
4. `seed_login_user`, `save_plan`'s `a2a_messages` insert, and `save_audit_event` — replace `INSERT OR IGNORE INTO x` with `INSERT INTO x` plus a trailing `ON CONFLICT DO NOTHING`, which both engines accept.
5. `initialize` — select the schema and the introspection query by dialect:

```python
def initialize(target: Path | str) -> None:
    with connect(target) as connection:
        connection.executescript(
            SCHEMA_POSTGRES if connection.dialect == "postgres" else SCHEMA_SQLITE
        )

        def existing_columns(table: str) -> set[str]:
            if connection.dialect == "postgres":
                return {
                    row["column_name"] for row in connection.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = ?", (table,)
                    ).fetchall()
                }
            return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
```

Then replace each of the three `PRAGMA table_info(...)` comprehensions with `existing_columns("users")`, `existing_columns("travel_requests")` and `existing_columns("planning_jobs")`. The `ALTER TABLE ... ADD COLUMN` statements are identical in both engines and do not change.

6. Lines 608, 656 and 701 use `fetchone()[0]`, which `dict_row` does not support. Alias the column and read it by name, for example:

```python
        users = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: 310+ passed, 0 failed. Everything still runs on SQLite; nothing yet selects Postgres.

- [ ] **Step 8: Commit**

```bash
git add flaskapp/database.py tests/test_schema_parity.py tests/test_database.py
git commit -m "feat: dialect layer and Postgres schema in database.py

One _Connection wrapper over sqlite3 or psycopg, translating ? placeholders
and generated-key retrieval. Both dialects now commit and close on a clean
context exit, which sqlite3 alone did not do."
```

---

### Task 4: Select the engine with DATABASE_URL

**Files:**
- Modify: `flaskapp/config.py:56` (the `DATABASE` line)
- Modify: `.env.example`
- Modify: `requirements.txt`
- Test: `tests/test_config.py` (extend)

**Interfaces:**
- Consumes: `is_postgres` from Task 3.
- Produces: `Config.DATABASE` is a `str` DSN when `DATABASE_URL` is set, otherwise a `Path`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_config.py
import importlib

from flaskapp.database import is_postgres


def test_database_url_selects_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    import flaskapp.config
    reloaded = importlib.reload(flaskapp.config)
    assert is_postgres(reloaded.Config.DATABASE)


def test_without_database_url_the_sqlite_path_is_used(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import flaskapp.config
    reloaded = importlib.reload(flaskapp.config)
    assert not is_postgres(reloaded.Config.DATABASE)
    assert str(reloaded.Config.DATABASE).endswith(".sqlite3")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v -k database`
Expected: `test_database_url_selects_postgres` FAILS — `DATABASE` is a `Path` regardless.

- [ ] **Step 3: Implement**

In `flaskapp/config.py`, replace the `DATABASE` assignment:

```python
    # A DSN wins when present; otherwise the local SQLite file. Keeping both in
    # one setting is what lets every database function take one target argument.
    DATABASE = os.getenv("DATABASE_URL", "").strip() or Path(
        os.getenv("DATABASE", "instance/travel_planner.sqlite3")
    )
```

Add to `.env.example`:

```dotenv
# Hosted Postgres. Leave blank for local SQLite development.
# Supabase: use the SESSION POOLER string (host contains pooler.supabase.com),
# not the direct connection — the direct endpoint is IPv6-only and Render
# cannot reach it. Percent-encode any /, @, # or ? in the password.
# The value itself is a secret and belongs in .env.secrets, never here.
DATABASE_URL=
```

Add to `requirements.txt` under the web application block:

```
psycopg[binary]>=3.2,<4
```

- [ ] **Step 4: Run tests**

Run: `python -m pip install -r requirements.txt && python -m pytest -q`
Expected: all green. Note `tests/test_config.py` reloads the module, so run the full suite to confirm no ordering leak.

- [ ] **Step 5: Commit**

```bash
git add flaskapp/config.py .env.example requirements.txt tests/test_config.py
git commit -m "feat: select Postgres via DATABASE_URL, SQLite otherwise"
```

---

### Task 5: Stop routing the DSN through Path()

`api.py:75` writes `str(config["DATABASE"])` into the job settings and `jobs.py:63` rebuilds it as `Path(...)`. `Path("postgresql://host/db")` collapses the double slash to `postgresql:/host/db`, which `is_postgres` then rejects — so every background planning thread would silently write to a stray local SQLite file while the request path used Supabase.

**Files:**
- Modify: `flaskapp/travel_ai/jobs.py:63`
- Modify: `flaskapp/travel_ai/service.py:19-22` (the `database_path` annotation)
- Test: `tests/test_jobs_database_target.py` (create)

**Interfaces:**
- Consumes: `is_postgres` from Task 3.
- Produces: `TravelPlanningService(database_path=...)` accepts `Path | str | None`; a DSN survives submission unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobs_database_target.py
"""A DSN must reach save_plan intact.

Path("postgresql://host/db") normalises the double slash away, and the result
is no longer recognised as a DSN — so planning silently falls back to SQLite
while the web request path talks to Postgres. Nothing raises.
"""

from pathlib import Path

from flaskapp.database import is_postgres

DSN = "postgresql://user:pass@aws-0-us-west-2.pooler.supabase.com:5432/postgres"


def test_path_round_trip_destroys_a_dsn():
    """Characterises the bug this task fixes."""
    assert not is_postgres(str(Path(DSN)))


def test_service_keeps_a_dsn_usable():
    from flaskapp.travel_ai.service import TravelPlanningService

    service = TravelPlanningService(
        provider="openai", api_key="k", model="m", temperature=None,
        timeout=1, trace_dir=Path("instance/traces"), database_path=DSN,
    )
    assert is_postgres(service.database_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_jobs_database_target.py -v`
Expected: the first test PASSES (it documents the hazard); the second FAILS or errors until the annotation and pass-through are fixed.

- [ ] **Step 3: Implement**

In `flaskapp/travel_ai/jobs.py:63`, stop coercing — `trace_dir` keeps its `Path(...)` because it is a real directory in both configurations:

```python
            trace_dir=Path(settings["trace_dir"]),
            # NOT Path(...): this may be a postgresql:// DSN, and Path would
            # normalise the // away and silently turn it back into SQLite.
            database_path=settings["database_path"],
```

In `flaskapp/travel_ai/service.py`, widen the annotation:

```python
                 database_path: Path | str | None = None, user_id: int | None = None,
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_jobs_database_target.py -v && python -m pytest -q`
Expected: both PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add flaskapp/travel_ai/jobs.py flaskapp/travel_ai/service.py tests/test_jobs_database_target.py
git commit -m "fix: do not route the database target through Path()

A postgresql:// DSN loses its double slash, so background planning threads
would write to a stray local SQLite file while the request path used Postgres."
```

---

### Task 6: Data migration script

**Files:**
- Create: `scripts/migrate_sqlite_to_postgres.py`
- Test: `tests/test_migration_script.py` (create)

**Interfaces:**
- Consumes: `connect`, `is_postgres` from Task 3.
- Produces: `TABLES: tuple[str, ...]` in dependency order; `copy_table(source, destination, table) -> tuple[int, int]` returning (read, written); `reset_sequences(destination) -> None`; `main(argv) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration_script.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_migration_script.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.migrate_sqlite_to_postgres'`.

- [ ] **Step 3: Write the script**

```python
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
    """Advance each identity sequence past the ids we inserted explicitly."""
    for table in IDENTITY_TABLES:
        destination.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
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
```

Create an empty `scripts/__init__.py` if one does not exist, so the test can import the module.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_migration_script.py -v && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_sqlite_to_postgres.py scripts/__init__.py tests/test_migration_script.py
git commit -m "feat: SQLite to Postgres migration script

Idempotent, foreign-key ordered, keys preserved, sequences reset, and per-table
counts verified so a partial copy exits non-zero rather than looking finished."
```

---

### Task 7: Postgres integration tests and CI

**Files:**
- Create: `tests/test_database_postgres.py`
- Modify: `.github/workflows/security.yml` (the `tests` job)

**Interfaces:**
- Consumes: everything from Tasks 3-6.
- Produces: a suite that runs against real Postgres when `TEST_DATABASE_URL` is set and skips cleanly when it is not.

- [ ] **Step 1: Write the test**

```python
# tests/test_database_postgres.py
"""The production dialect, exercised for real.

Skipped without TEST_DATABASE_URL so local runs stay offline and SQLite-only.
CI sets it, so the dialect that actually ships is never untested.
"""

import os
import uuid

import pytest

from flaskapp.database import (
    connect, create_user, get_platform_dashboard, initialize, save_plan,
)

DSN = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")


@pytest.fixture()
def database():
    initialize(DSN)
    yield DSN
    with connect(DSN) as db:
        for table in (
            "plan_feedback", "agent_runs", "intake_messages", "planning_jobs",
            "audit_events", "a2a_messages", "options", "agent_findings",
            "travel_plans", "travel_requests", "users",
        ):
            db.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")


def test_initialize_creates_every_table(database):
    with connect(database) as db:
        tables = {
            row["table_name"] for row in db.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            ).fetchall()
        }
    assert {"users", "travel_requests", "travel_plans", "agent_findings",
            "options", "a2a_messages", "audit_events", "planning_jobs",
            "intake_messages", "agent_runs", "plan_feedback"} <= tables


def test_initialize_is_idempotent(database):
    initialize(database)  # must not raise


def test_generated_keys_come_back(database):
    with connect(database) as db:
        user_id = db.insert_returning_id(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("A", f"{uuid.uuid4()}@example.com", "hash"),
        )
    assert isinstance(user_id, int) and user_id > 0


def test_email_uniqueness_is_case_insensitive(database):
    with connect(database) as db:
        db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("Case@Example.com", "hash"),
        )
    with connect(database) as db:
        rows = db.execute(
            "SELECT id FROM users WHERE email = ?", ("case@example.com",)
        ).fetchall()
    assert len(rows) == 1, "CITEXT should match regardless of case"


def test_default_timestamp_matches_the_sqlite_format(database):
    """The dashboard slices these strings; the formats must be identical."""
    with connect(database) as db:
        db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (f"{uuid.uuid4()}@example.com", "hash"),
        )
    with connect(database) as db:
        created = db.execute(
            "SELECT created_at FROM users LIMIT 1"
        ).fetchone()["created_at"]
    assert len(created) == 19, f"expected YYYY-MM-DD HH:MM:SS, got {created!r}"
    assert created[4] == "-" and created[10] == " " and created[13] == ":"


def test_dashboard_queries_run(database):
    """Proves the SUBSTR rewrite is valid Postgres, not just valid SQLite.

    Asserted against the shape the admin page consumes, so a query that parses
    but returns the wrong thing still fails here.
    """
    dashboard = get_platform_dashboard(database)
    assert isinstance(dashboard, dict)
    assert dashboard, "the dashboard returned nothing at all"
    for key, value in dashboard.items():
        assert value is not None, f"{key} came back None"


def test_create_user_rejects_a_duplicate_email(database, monkeypatch):
    """create_user's except clause must catch psycopg's error, not sqlite3's."""
    from flaskapp import create_app
    from flaskapp.config import Config

    class PostgresConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        DATABASE = DSN

    app = create_app(PostgresConfig)
    with app.app_context():
        first = create_user("A", "dupe@example.com", "hash", "Singapore", "1990-01-01")
        second = create_user("B", "DUPE@example.com", "hash", "Singapore", "1990-01-01")
    assert first is not None
    assert second is None, "a duplicate email must return None, not raise"
```

- [ ] **Step 2: Run test to verify it skips locally**

Run: `python -m pytest tests/test_database_postgres.py -v`
Expected: all tests SKIPPED with "TEST_DATABASE_URL is not set".

- [ ] **Step 3: Add Postgres to the CI tests job**

In `.github/workflows/security.yml`, inside the `tests` job, add above `steps:`:

```yaml
    # The deployed dialect. Local runs stay SQLite-only by choice, so this job
    # is the only place the Postgres code paths execute before production.
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: travel_planner_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
```

And give the pytest step the DSN:

```yaml
      - name: Run pytest
        env:
          TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:5432/travel_planner_test
        run: python -m pytest -q --junitxml=pytest-report.xml
```

- [ ] **Step 4: Verify against a local Postgres**

If Docker is available, confirm the tests genuinely pass rather than merely skip:

```bash
docker run --rm -d --name pg-test -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=travel_planner_test -p 5432:5432 postgres:16
sleep 5
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/travel_planner_test \
  python -m pytest tests/test_database_postgres.py -v
docker rm -f pg-test
```

Expected: all PASS. Without Docker, rely on CI and note it in the commit.

- [ ] **Step 5: Commit**

```bash
git add tests/test_database_postgres.py .github/workflows/security.yml
git commit -m "test: exercise the Postgres dialect in CI

Local runs stay SQLite-only by design, so CI is the only place the deployed
code paths execute before production."
```

---

### Task 8: Render wiring and the live migration

**Files:**
- Modify: `render.yaml`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: a deployed service backed by Supabase.

- [ ] **Step 1: Add DATABASE_URL to the blueprint**

In `render.yaml`, add to `envVars`:

```yaml
      # Supabase session pooler DSN. Set by hand in the dashboard; never here.
      - key: DATABASE_URL
        sync: false
```

Delete the commented `DATABASE`/`TRACE_DIR` override lines and the commented `disk:` block — with state in Supabase, the ephemeral disk no longer matters. Leave the `--workers 1` comment untouched; it is unrelated and still true.

- [ ] **Step 2: Document the setup in README.md**

Add after the "Local database" section:

```markdown
## Hosted database

Set `DATABASE_URL` to a Postgres DSN and the application uses it instead of
SQLite; leave it unset and local development is unchanged. Supabase users must
use the **session pooler** string (its host contains `pooler.supabase.com`) —
the direct endpoint is IPv6-only and Render cannot reach it. Percent-encode any
`/`, `@`, `#` or `?` in the password.

Copy the existing local data across once:

```powershell
python scripts/migrate_sqlite_to_postgres.py --destination $env:DATABASE_URL --dry-run
python scripts/migrate_sqlite_to_postgres.py --destination $env:DATABASE_URL
```

The script is idempotent and prints per-table counts, exiting non-zero if any
table ends up short.
```

- [ ] **Step 3: Run the migration for real**

```bash
set -a && . ./.env.secrets && set +a
python scripts/migrate_sqlite_to_postgres.py --destination "$DATABASE_URL" --dry-run
python scripts/migrate_sqlite_to_postgres.py --destination "$DATABASE_URL"
```

Expected: the dry run reports 2 users, 16 travel_requests, 16 travel_plans, 64 agent_findings, 227 options, 128 a2a_messages, 265 audit_events, 17 planning_jobs, 2 intake_messages, 85 agent_runs, 6 plan_feedback — 828 rows. The real run reports matching per-table totals and exits 0.

- [ ] **Step 4: Verify the migrated data**

```bash
set -a && . ./.env.secrets && set +a
python -c "
from flaskapp.database import get_admin_activity, connect
import os
dsn = os.environ['DATABASE_URL']
print('requests:', len(get_admin_activity(dsn)))
with connect(dsn) as db:
    print('users:', db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'])
"
```

Expected: non-zero counts matching the SQLite source, proving reads work through the same functions the app uses.

- [ ] **Step 5: Commit and deploy**

```bash
git add render.yaml README.md
git commit -m "feat: point the Render deployment at Supabase Postgres"
git push origin subbu-18Aug
```

Then in the Render dashboard set `DATABASE_URL` to the session pooler DSN and trigger a deploy.

- [ ] **Step 6: Verify the deployment survives a restart**

1. Sign in to the deployed app and register a new account.
2. Trigger a manual deploy from the Render dashboard.
3. Sign in again with that account.

Expected: the account still exists. This is the entire point of the migration — if it fails, `DATABASE_URL` is not reaching the app, and the deploy log will show SQLite being initialised instead.

---

## Notes for the executor

- **Task order matters.** Task 1 must precede Task 4: once `DATABASE_URL` is honoured, an import-time `create_app()` would run DDL against production from every test.
- **Never edit a test to make it pass.** A failing parity or dashboard test means the schema or query is wrong.
- **`.env.secrets` is gitignored and must stay that way.** Run `git status --short` before every commit and confirm it never appears.
- **The 308 pre-existing tests are the safety net.** Any task that leaves them red is not finished.
