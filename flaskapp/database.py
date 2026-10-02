"""SQLite or Postgres persistence for users, travel plans, agent output, and audit data."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import click
from flask import Flask, current_app
from werkzeug.security import check_password_hash

# psycopg is imported lazily inside connect(), so a SQLite-only environment
# never needs it installed. This tuple is what `except` clauses catch.
IntegrityError: tuple[type[Exception], ...] = (sqlite3.IntegrityError,)
try:  # pragma: no cover - depends on the installed extras
    import psycopg

    IntegrityError = (sqlite3.IntegrityError, psycopg.errors.UniqueViolation)
except ImportError:
    pass

SCHEMA_SQLITE = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    country TEXT,
    birthday TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS travel_requests (
    id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    origin TEXT,
    destination TEXT NOT NULL,
    -- Countries above, cities here. NULLable because city intake postdates
    -- these rows and a country-only request stays valid.
    origin_city TEXT,
    destination_city TEXT,
    plan_scope TEXT NOT NULL DEFAULT 'both',
    departure_date TEXT NOT NULL,
    return_date TEXT NOT NULL,
    travellers INTEGER NOT NULL CHECK (travellers BETWEEN 1 AND 20),
    traveller_ages_json TEXT NOT NULL DEFAULT '[]',
    traveller_genders_json TEXT NOT NULL DEFAULT '[]',
    traveller_accessibility_needs_json TEXT NOT NULL DEFAULT '[]',
    budget REAL,
    currency TEXT NOT NULL,
    preferences_json TEXT NOT NULL DEFAULT '[]',
    accessibility_needs_json TEXT NOT NULL DEFAULT '[]',
    refinement_notes_json TEXT NOT NULL DEFAULT '[]',
    risk_tolerance TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS travel_plans (
    request_id TEXT PRIMARY KEY REFERENCES travel_requests(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    itinerary_json TEXT NOT NULL,
    estimated_total_cost REAL,
    currency TEXT,
    rationale_json TEXT NOT NULL,
    alternatives_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    limitations_json TEXT NOT NULL DEFAULT '[]',
    safety_passed INTEGER NOT NULL,
    safety_checks_json TEXT NOT NULL DEFAULT '[]',
    safety_warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES travel_requests(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    summary TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    -- When save_plan() stored this row, which is when the plan was saved, not
    -- when the agent finished: that is agent_runs.completed_at. Nullable with
    -- no default so a migrated database matches a fresh one; see initialize().
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id INTEGER NOT NULL REFERENCES agent_findings(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    estimated_cost REAL,
    currency TEXT,
    source_urls_json TEXT NOT NULL DEFAULT '[]',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    limitations_json TEXT NOT NULL DEFAULT '[]',
    selection_factors_json TEXT NOT NULL DEFAULT '[]',
    -- What kind of option this is, as asserted by the builder that made it.
    -- Nullable: rows written before builders classified themselves genuinely
    -- have no category, and NULL says that rather than guessing one.
    category TEXT,
    -- Which airport an option lands at or serves. Defaulted rather than
    -- nullable: rows written before packages existed served no airport, and ''
    -- says that without a caller needing to handle None.
    airport TEXT NOT NULL DEFAULT '',
    -- Copied from the owning finding so "every option for this request" is one
    -- filter instead of a join. Nullable because ADD COLUMN cannot add NOT NULL
    -- to a table with rows; initialize() backfills it from agent_findings.
    request_id TEXT REFERENCES travel_requests(id) ON DELETE CASCADE,
    -- Same meaning and reasoning as agent_findings.created_at.
    created_at TEXT
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
    payload_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    agent TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    previous_hash TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS planning_jobs (
    request_id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    error_type TEXT,
    session_status TEXT NOT NULL DEFAULT 'active',
    session_ended_at TEXT,
    worker TEXT,
    heartbeat_at REAL,
    response_json TEXT
);

CREATE TABLE IF NOT EXISTS intake_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES planning_jobs(request_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES planning_jobs(request_id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    response_json TEXT,
    error_type TEXT,
    UNIQUE(request_id, agent)
);

CREATE TABLE IF NOT EXISTS plan_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE REFERENCES planning_jobs(request_id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
-- Flight Agent monitoring/eval history. `run_type` distinguishes sampled
-- production traffic from scheduled golden-scenario drift replays and one-off
-- offline evals, since all three share the same shape; request_id is
-- nullable because replay/offline runs are not tied to a real traveller
-- request. request_id references planning_jobs, not travel_requests: the
-- Flight Agent writes its row mid-plan, and travel_requests only gains its row
-- in save_plan once the whole graph has finished, so that FK failed on every
-- live run. planning_jobs exists from submit time (or from the A2A entry
-- point's ensure_planning_job), the same row agent_runs already relies on.
-- Existing databases are moved across by _repoint_flight_eval_run_fk.
-- Config columns (mode, inventory_source, llm_provider/model) are
-- recorded per row, not assumed constant, since all three can change between
-- runs. `estimated_cost_usd` is stored rather than derived from tokens at
-- query time, since model pricing moves and a run's cost should reflect what
-- it paid, not today's price applied retroactively.
CREATE TABLE IF NOT EXISTS flight_agent_eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL CHECK (run_type IN ('production', 'golden_replay', 'offline_eval')),
    request_id TEXT REFERENCES planning_jobs(request_id) ON DELETE SET NULL,
    scenario_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    flight_agent_mode TEXT NOT NULL CHECK (flight_agent_mode IN ('structured', 'agentic', 'auto')),
    inventory_source TEXT NOT NULL CHECK (inventory_source IN ('seed', 'duffel')),
    llm_provider TEXT,
    llm_model TEXT,
    -- Whether auto mode's _has_empty_leg check escalated this run into the
    -- tool loop, and why.
    escalated_to_loop INTEGER NOT NULL DEFAULT 0,
    escalation_reason TEXT,
    latency_ms INTEGER NOT NULL,
    llm_turns INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    provider_calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'path2_fallback', 'budget_exhausted', 'error')),
    options_returned INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    guardrail_input_result TEXT CHECK (guardrail_input_result IN ('pass', 'medium', 'high', 'blocked')),
    guardrail_output_result TEXT CHECK (guardrail_output_result IN ('pass', 'medium', 'high', 'blocked')),
    guardrail_grounding_result TEXT CHECK (guardrail_grounding_result IN ('pass', 'fail')),
    guardrail_block_reason TEXT,
    -- NULL unless run_type = 'golden_replay'; golden_diff_json is populated
    -- only when golden_scenario_match is false.
    golden_scenario_match INTEGER,
    golden_diff_json TEXT,
    -- Pointer into the tamper-evident trace chain. Kept even though TRACE_DIR
    -- itself is ephemeral on Render, so this row outlives the JSONL file.
    trace_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_flight_eval_created ON flight_agent_eval_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_flight_eval_run_type ON flight_agent_eval_runs(run_type);
CREATE INDEX IF NOT EXISTS idx_flight_eval_outcome ON flight_agent_eval_runs(outcome);
CREATE INDEX IF NOT EXISTS idx_flight_eval_scenario ON flight_agent_eval_runs(scenario_id)
    WHERE scenario_id IS NOT NULL;
"""

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
    origin TEXT,
    destination TEXT NOT NULL,
    origin_city TEXT,
    destination_city TEXT,
    plan_scope TEXT NOT NULL DEFAULT 'both',
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
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    -- When save_plan() stored this row, which is when the plan was saved, not
    -- when the agent finished: that is agent_runs.completed_at. Nullable with
    -- no default so a migrated database matches a fresh one; see initialize().
    created_at TEXT
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
    selection_factors_json TEXT NOT NULL DEFAULT '[]',
    -- What kind of option this is, as asserted by the builder that made it.
    -- Nullable: rows written before builders classified themselves genuinely
    -- have no category, and NULL says that rather than guessing one.
    category TEXT,
    -- Which airport an option lands at or serves. Defaulted rather than
    -- nullable: rows written before packages existed served no airport, and ''
    -- says that without a caller needing to handle None.
    airport TEXT NOT NULL DEFAULT '',
    -- Copied from the owning finding so "every option for this request" is one
    -- filter instead of a join. Nullable because ADD COLUMN cannot add NOT NULL
    -- to a table with rows; initialize() backfills it from agent_findings.
    request_id TEXT REFERENCES travel_requests(id) ON DELETE CASCADE,
    -- Same meaning and reasoning as agent_findings.created_at.
    created_at TEXT
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
    session_ended_at TEXT,
    worker TEXT,
    heartbeat_at DOUBLE PRECISION,
    response_json TEXT
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
-- Flight Agent monitoring/eval history — see the matching block in
-- SCHEMA_SQLITE for the field-by-field rationale.
CREATE TABLE IF NOT EXISTS flight_agent_eval_runs (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    run_type TEXT NOT NULL CHECK (run_type IN ('production', 'golden_replay', 'offline_eval')),
    request_id TEXT REFERENCES planning_jobs(request_id) ON DELETE SET NULL,
    scenario_id TEXT,
    created_at TEXT NOT NULL DEFAULT {_NOW},
    flight_agent_mode TEXT NOT NULL CHECK (flight_agent_mode IN ('structured', 'agentic', 'auto')),
    inventory_source TEXT NOT NULL CHECK (inventory_source IN ('seed', 'duffel')),
    llm_provider TEXT,
    llm_model TEXT,
    escalated_to_loop INTEGER NOT NULL DEFAULT 0,
    escalation_reason TEXT,
    latency_ms INTEGER NOT NULL,
    llm_turns INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    provider_calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd DOUBLE PRECISION,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'path2_fallback', 'budget_exhausted', 'error')),
    options_returned INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    guardrail_input_result TEXT CHECK (guardrail_input_result IN ('pass', 'medium', 'high', 'blocked')),
    guardrail_output_result TEXT CHECK (guardrail_output_result IN ('pass', 'medium', 'high', 'blocked')),
    guardrail_grounding_result TEXT CHECK (guardrail_grounding_result IN ('pass', 'fail')),
    guardrail_block_reason TEXT,
    golden_scenario_match INTEGER,
    golden_diff_json TEXT,
    trace_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_flight_eval_created ON flight_agent_eval_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_flight_eval_run_type ON flight_agent_eval_runs(run_type);
CREATE INDEX IF NOT EXISTS idx_flight_eval_outcome ON flight_agent_eval_runs(outcome);
CREATE INDEX IF NOT EXISTS idx_flight_eval_scenario ON flight_agent_eval_runs(scenario_id)
    WHERE scenario_id IS NOT NULL;
"""


POSTGRES_SCHEMES = ("postgresql://", "postgres://")


_JSON_EXTRACT = re.compile(r"json_extract\(\s*(.+?)\s*,\s*'\$\.(\w+)'\s*\)", re.IGNORECASE)


def to_postgres_sql(sql: str) -> str:
    """Rewrite one dialect-neutral query into the Postgres it means.

    Three substitutions, and deliberately no more. Every one of them replaces a
    construct SQLite accepts and Postgres does not; none of them introduces a
    `%` or a `?`, so none can collide with parameter binding.

    * `CURRENT_TIMESTAMP` -> the same `to_char(...)` expression SCHEMA_POSTGRES
      defaults to. Postgres has no assignment cast from timestamptz to the TEXT
      columns these values land in, so the bare keyword would raise; and its
      native rendering (`2026-08-18 14:30:00.123456+00`) would break the
      `SUBSTR(x, 1, 10)` slicing the dashboards do.
    * `json_extract(expr, '$.field')` -> `(expr)::json->>'field'`. Postgres has
      no json_extract at all. `->>` returns text and yields NULL for a missing
      key, which is what json_extract does for the four string fields
      get_recent_feedback reads out of planning_jobs.request_json.
    * `?` -> `%s`, psycopg's placeholder.

    Order among the first two is irrelevant — neither one's output contains the
    other's input — but the placeholder rewrite must stay last so that nothing
    downstream can manufacture or consume a `%s`.
    """
    sql = sql.replace("CURRENT_TIMESTAMP", _NOW)
    sql = _JSON_EXTRACT.sub(r"(\1)::json->>'\2'", sql)
    return sql.replace("?", "%s")


def is_postgres(target: Path | str) -> bool:
    """True when the target names a Postgres DSN rather than a SQLite file."""
    return isinstance(target, str) and target.startswith(POSTGRES_SCHEMES)


class _Connection:
    """The slice of sqlite3.Connection this module actually uses.

    Deliberately small — execute, commit, rollback, close, and context-manager
    entry/exit. Do not widen it speculatively.

    Both dialects commit AND close on a clean context exit. sqlite3 alone would
    commit and leave the connection open; psycopg alone would close. Making
    them agree is what lets `with connect(...)` mean one thing in every call
    site. On Postgres "close" returns the connection to this process's pool
    (see `_postgres_pool`), so a `with` block borrows rather than opens.
    """

    def __init__(self, raw, dialect: str):
        self._raw = raw
        self.dialect = dialect

    def execute(self, sql: str, parameters: tuple = ()):
        if self.dialect == "postgres":
            cursor = self._raw.cursor()
            cursor.execute(to_postgres_sql(sql), parameters)
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


# Which Postgres schema the tables live in. Empty means the server default,
# which is what every SQLite deployment and the stock Supabase layout use.
#
# Applied per connection rather than left to `ALTER ROLE ... SET search_path`,
# because Supabase fronts the database with pgbouncer: a role default is read
# when a *backend* session starts, so pooled connections opened before the
# change keep serving the old path and the tables appear to vanish. Issuing it
# on the connection we just took out of the pool is the only version that is
# true for every connection immediately.
DATABASE_SCHEMA = os.getenv("DATABASE_SCHEMA", "").strip()


# Connections per process kept open and reused, instead of one opened and
# closed per call. A single plan's audit trail alone used to open ~31; with
# web and agents each autoscaling to 3 pods, a burst could approach Supabase's
# limit of 60. Per process this is the ceiling, not a reservation: the pool
# keeps 1 open and grows to this only while that many threads are inside a
# `with connect(...)` at once, which is brief. Worst case 3 web + 3 agents
# pods x 5 = 30. Set DATABASE_POOL_SIZE=0 to go back to a connection per call.
DATABASE_POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "5"))
# How long a caller waits for a free connection (or for the database to come
# back) before the request fails.
DATABASE_POOL_TIMEOUT_SECONDS = float(os.getenv("DATABASE_POOL_TIMEOUT_SECONDS", "30"))

# Shown in pg_stat_activity, so the database can say how many connections
# this app holds: SELECT count(*) FROM pg_stat_activity
#                 WHERE application_name = 'travel-planner'
APPLICATION_NAME = "travel-planner"

_pools: dict[str, Any] = {}
_pools_lock = threading.Lock()


def _configure_postgres(raw) -> None:
    """Session settings every Postgres connection needs, applied once when it
    is opened — pooled connections keep them for life."""
    if DATABASE_SCHEMA:
        from psycopg import sql

        # Identifier() quotes it correctly; the schema name is
        # case-sensitive and unquoted Postgres folds it to lowercase.
        raw.execute(sql.SQL("SET search_path TO {}, public, extensions").format(
            sql.Identifier(DATABASE_SCHEMA)
        ))
        raw.commit()
    # AVG() and ROUND() return `numeric` on Postgres and `float` on
    # SQLite. psycopg maps numeric to Decimal, and Flask's JSON
    # provider renders Decimal as a *string*, so the admin charts
    # would receive "92.3" instead of 92.3. No column in either schema
    # is numeric — only aggregate results are — so loading numeric as
    # float loses nothing and matches SQLite exactly.
    try:  # pragma: no cover - needs psycopg installed
        from psycopg.types.numeric import FloatLoader

        raw.adapters.register_loader("numeric", FloatLoader)
    except (ImportError, AttributeError):  # pragma: no cover
        pass


def _postgres_pool(dsn: str):
    """This process's pool for `dsn`, created on first use.

    First use, not import: under gunicorn the pool must be born in the worker,
    because its connections and background threads do not survive a fork.
    `close_returns=True` is what keeps every call site unchanged: `_Connection`
    commits or rolls back and then calls close(), and on a pooled connection
    close() hands it back instead of ending it. The pool rolls back anything
    left uncommitted and checks a connection is alive before lending it.
    """
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=DATABASE_POOL_SIZE,
                kwargs={"row_factory": dict_row, "application_name": APPLICATION_NAME},
                configure=_configure_postgres,
                check=ConnectionPool.check_connection,
                close_returns=True,
                timeout=DATABASE_POOL_TIMEOUT_SECONDS,
                # Recycle idle and long-lived connections, so a Supabase pooler
                # restart or an idle cut-off never leaves us holding dead ones.
                max_idle=300,
                max_lifetime=1800,
                name="travel-planner",
                open=True,
            )
            _pools[dsn] = pool
        return pool


def connect(target: Path | str) -> _Connection:
    """Open a connection to a SQLite path, or borrow one for a Postgres DSN."""
    if is_postgres(target):
        import psycopg

        try:
            if DATABASE_POOL_SIZE > 0:
                raw = _postgres_pool(target).getconn()
            else:
                from psycopg.rows import dict_row

                raw = psycopg.connect(target, row_factory=dict_row,
                                      application_name=APPLICATION_NAME)
                _configure_postgres(raw)
        except psycopg.OperationalError as error:
            # PoolTimeout is an OperationalError too. The failure is almost
            # always the pooler/direct distinction, and the driver's own
            # message ("could not translate host name") does not hint at it.
            # Say so once, here, rather than in a runbook.
            raise psycopg.OperationalError(f"{error}\n\n{CONNECTION_HELP}") from error
        return _Connection(raw, "postgres")
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return _Connection(connection, "sqlite")


def _app_db() -> _Connection:
    """A short-lived connection for the auth routes.

    These used to share one connection per request (Flask's `g.db`), held until
    the request ended. With a pool that is a deadlock waiting to happen: a
    request holding one connection and asking for a second (the login page's
    template checks admin status) can wait for ever once every pooled
    connection is held that way. Every function here takes and returns its
    own, like the rest of this module.
    """
    return connect(current_app.config["DATABASE"])


def _rebuild_sqlite_table(connection, table: str, edit) -> None:
    """SQLite's twelve-step table rebuild, with `edit` applied to the CREATE.

    SQLite has no `ALTER COLUMN` and no way to alter a constraint, so changing
    either means recreating the table
    (https://www.sqlite.org/lang_altertable.html#otheralter). `edit` takes the
    stored CREATE statement and returns the version to build; it must raise if
    it cannot make the change, because a rebuild that silently changed nothing
    still drops and recreates the table and would run again on every startup.

    Shared by the two migrations below so the steps that are easy to omit live
    in one place:

    * Foreign keys go OFF first. `connect()` turns them on, and with them on
      `DROP TABLE` runs an implicit DELETE that fires every `ON DELETE CASCADE`
      pointing at the table. The pragma is a silent no-op inside a transaction,
      hence the commit before it.
    * The new table comes from the stored CREATE, not from `PRAGMA table_info`,
      which carries neither foreign keys nor CHECK constraints. Indexes and
      triggers die with the DROP, so they are recreated from their own SQL.
    * Rows are copied BY NAME: these tables have gained columns over time and
      the two shapes need not agree.
    * `PRAGMA foreign_key_check` at the end, inside the transaction, so a
      rebuild that broke a reference rolls back instead of persisting.
    """
    columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()[0]
    edited = edit(table_sql)
    if edited == table_sql:
        # An edit that changed nothing would drop and recreate the table
        # identically, leave the old definition in place, and do it again on
        # every startup. Callers raise their own, more specific error first;
        # this is the backstop for the one that forgets.
        raise RuntimeError(f"Cannot rebuild {table}: the edit changed nothing")
    migrated_sql, renamed = re.subn(
        rf"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?{table}\b[\"`\]]?",
        f"CREATE TABLE {table}_migrated", edited, flags=re.IGNORECASE,
    )
    if renamed != 1:
        raise RuntimeError(f"Cannot rebuild {table}: unrecognised table definition")
    dependents = [
        row[0] for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name = ? "
            "AND type IN ('index', 'trigger') AND sql IS NOT NULL", (table,)
        ).fetchall()
    ]
    names = ", ".join(row[1] for row in columns)

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN")
        connection.execute(migrated_sql)
        connection.execute(
            f"INSERT INTO {table}_migrated ({names}) SELECT {names} FROM {table}"
        )
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE {table}_migrated RENAME TO {table}")
        for statement in dependents:
            connection.execute(statement)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"Rebuilding {table} left {len(violations)} foreign-key violation(s)"
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _allow_null_origin(connection) -> None:
    """Drop the NOT NULL constraint on `travel_requests.origin`.

    A hotel-only request has no departure country, so the column must accept
    NULL. Postgres alters in place; SQLite rebuilds the table through
    `_rebuild_sqlite_table`, which carries the foreign-key and transaction
    safeguards that rebuild needs.
    """
    if connection.dialect == "postgres":
        connection.execute(
            "ALTER TABLE travel_requests ALTER COLUMN origin DROP NOT NULL"
        )
        return
    columns = connection.execute("PRAGMA table_info(travel_requests)").fetchall()
    if not columns or not any(row[1] == "origin" and row[3] for row in columns):
        return  # Already nullable, or the table does not exist yet.

    def relax_origin(table_sql: str) -> str:
        table_sql, relaxed = re.subn(
            r"\borigin(\s+TEXT)\s+NOT\s+NULL\b", r"origin\1", table_sql, flags=re.IGNORECASE
        )
        if relaxed != 1:
            # Refuse rather than guess: every schema that ever shipped declared
            # `origin TEXT NOT NULL`, so anything else is a table this was not
            # written for.
            raise RuntimeError(
                "Cannot relax travel_requests.origin: unrecognised column definition"
            )
        return table_sql

    _rebuild_sqlite_table(connection, "travel_requests", relax_origin)


def _repoint_flight_eval_run_fk(connection) -> None:
    """Move `flight_agent_eval_runs.request_id` from travel_requests to planning_jobs.

    `CREATE TABLE IF NOT EXISTS` leaves a table created under the old FK
    untouched, and that FK rejects every live insert (see the comment above the
    table in SCHEMA_SQLITE), so an existing database has to be migrated
    explicitly. A no-op once the FK already points at planning_jobs.

    Any request_id with no planning_jobs row is set to NULL first, which is what
    ON DELETE SET NULL would have left behind anyway; `trace_id` still carries
    the id. Without that, adding the new constraint would fail on such a row.
    """
    orphan_update = (
        "UPDATE flight_agent_eval_runs SET request_id = NULL "
        "WHERE request_id IS NOT NULL "
        "AND request_id NOT IN (SELECT request_id FROM planning_jobs)"
    )
    if connection.dialect == "postgres":
        stale = [
            row["conname"] for row in connection.execute(
                # `current_schema()`, for the same reason as `existing_columns`.
                """SELECT con.conname
                   FROM pg_constraint con
                   JOIN pg_class child ON child.oid = con.conrelid
                   JOIN pg_namespace nsp ON nsp.oid = child.relnamespace
                   JOIN pg_class parent ON parent.oid = con.confrelid
                   WHERE con.contype = 'f' AND nsp.nspname = current_schema()
                     AND child.relname = 'flight_agent_eval_runs'
                     AND parent.relname = 'travel_requests'"""
            ).fetchall()
        ]
        if not stale:
            return
        for name in stale:
            escaped = name.replace('"', '""')
            connection.execute(f'ALTER TABLE flight_agent_eval_runs DROP CONSTRAINT "{escaped}"')
        connection.execute(orphan_update)
        connection.execute(
            "ALTER TABLE flight_agent_eval_runs ADD CONSTRAINT flight_agent_eval_runs_request_id_fkey "
            "FOREIGN KEY (request_id) REFERENCES planning_jobs(request_id) ON DELETE SET NULL"
        )
        return
    references = connection.execute("PRAGMA foreign_key_list(flight_agent_eval_runs)").fetchall()
    if not any(row[2] == "travel_requests" for row in references):
        return
    # Before the rebuild, not inside it: the new constraint would reject any
    # request_id with no planning_jobs row, and this is what ON DELETE SET NULL
    # would have left behind anyway.
    connection.execute(orphan_update)

    def repoint(table_sql: str) -> str:
        migrated, swapped = re.subn(
            r"REFERENCES\s+[\"`\[]?travel_requests[\"`\]]?\s*\(\s*[\"`\[]?id[\"`\]]?\s*\)",
            "REFERENCES planning_jobs(request_id)", table_sql, count=1, flags=re.IGNORECASE,
        )
        if swapped != 1:
            # PRAGMA foreign_key_list said the old reference is there, so not
            # finding it in the CREATE means a definition this was not written
            # for. Refusing keeps the table as it is; guessing would drop and
            # recreate it unchanged on every startup.
            raise RuntimeError(
                "Cannot repoint flight_agent_eval_runs.request_id: unrecognised reference"
            )
        return migrated

    _rebuild_sqlite_table(connection, "flight_agent_eval_runs", repoint)


def _add_trace_columns(connection, existing_columns) -> None:
    """Add and backfill `options.request_id` and both `created_at` columns.

    Every step is safe to repeat, because initialize() runs on every start.

    The columns are added without a default. A "now" default on ADD COLUMN would
    stamp every existing Postgres row with the deploy time, which is a false
    fact, and SQLite rejects a non-constant default there anyway.

    The backfills are exact, not estimates. `options.request_id` comes from the
    option's own finding. `created_at` comes from `travel_plans.created_at`
    because save_plan() is the only writer of all three tables and inserts the
    plan, its findings and its options in one pass. A row with no plan row
    stays NULL rather than being given a guessed time.

    The index is created here and not in the schema constants: those run first,
    and on an existing database the column would not exist yet.
    """
    option_columns = existing_columns("options")
    if "request_id" not in option_columns:
        connection.execute(
            "ALTER TABLE options ADD COLUMN request_id TEXT "
            "REFERENCES travel_requests(id) ON DELETE CASCADE"
        )
    if "created_at" not in option_columns:
        connection.execute("ALTER TABLE options ADD COLUMN created_at TEXT")
    if "created_at" not in existing_columns("agent_findings"):
        connection.execute("ALTER TABLE agent_findings ADD COLUMN created_at TEXT")

    connection.execute(
        """UPDATE options SET request_id = (
               SELECT f.request_id FROM agent_findings f WHERE f.id = options.finding_id
           ) WHERE request_id IS NULL"""
    )
    connection.execute(
        """UPDATE agent_findings SET created_at = (
               SELECT p.created_at FROM travel_plans p
               WHERE p.request_id = agent_findings.request_id
           ) WHERE created_at IS NULL"""
    )
    connection.execute(
        """UPDATE options SET created_at = (
               SELECT f.created_at FROM agent_findings f WHERE f.id = options.finding_id
           ) WHERE created_at IS NULL"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_options_request ON options(request_id)"
    )


def initialize(target: Path | str) -> None:
    with connect(target) as connection:
        connection.executescript(
            SCHEMA_POSTGRES if connection.dialect == "postgres" else SCHEMA_SQLITE
        )

        def existing_columns(table: str) -> set[str]:
            if connection.dialect == "postgres":
                return {
                    row["column_name"] for row in connection.execute(
                        # `current_schema()`, not a literal: the tables live in
                        # whichever schema `search_path` selects, and pinning this
                        # to `public` makes every column look missing the moment
                        # they are anywhere else.
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = ?", (table,)
                    ).fetchall()
                }
            return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

        columns = existing_columns("users")
        for name, definition in (
            ("name", "TEXT"), ("country", "TEXT"), ("birthday", "TEXT"),
            ("is_admin", "INTEGER NOT NULL DEFAULT 0")
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
        request_columns = existing_columns("travel_requests")
        for name in (
            "traveller_ages_json", "traveller_genders_json",
            "traveller_accessibility_needs_json", "refinement_notes_json",
        ):
            if name not in request_columns:
                connection.execute(
                    f"ALTER TABLE travel_requests ADD COLUMN {name} TEXT NOT NULL DEFAULT '[]'"
                )
        # Cities are NULLable, unlike the country columns: rows written before
        # city intake existed have no city and must stay readable. A NULL here
        # means "country granularity", which the flight adapter handles by
        # falling back to the country's main gateway.
        if "category" not in existing_columns("options"):
            connection.execute("ALTER TABLE options ADD COLUMN category TEXT")
        if "airport" not in existing_columns("options"):
            connection.execute(
                "ALTER TABLE options ADD COLUMN airport TEXT NOT NULL DEFAULT ''"
            )
        _add_trace_columns(connection, existing_columns)
        # `origin` was NOT NULL until hotel-only scope existed: a stay has no
        # departure country. Postgres can relax the constraint in place;
        # SQLite cannot, so the table is rebuilt with its rows copied across.
        _allow_null_origin(connection)
        # Which specialists the request asked for. NOT NULL with a default
        # rather than nullable: a row written before selective dispatch existed
        # ran every specialist, and `both` is exactly what that means. A NULL
        # here would be indistinguishable from "nobody recorded it".
        if "plan_scope" not in request_columns:
            connection.execute(
                "ALTER TABLE travel_requests ADD COLUMN plan_scope TEXT NOT NULL DEFAULT 'both'"
            )
        for name in ("origin_city", "destination_city"):
            if name not in request_columns:
                connection.execute(f"ALTER TABLE travel_requests ADD COLUMN {name} TEXT")
        job_columns = existing_columns("planning_jobs")
        if "session_status" not in job_columns:
            connection.execute(
                "ALTER TABLE planning_jobs ADD COLUMN session_status TEXT NOT NULL DEFAULT 'active'"
            )
        if "session_ended_at" not in job_columns:
            connection.execute("ALTER TABLE planning_jobs ADD COLUMN session_ended_at TEXT")
        # Job state that any web pod can read (jobs.py). Nullable, no default:
        # an old row has no worker, heartbeat or stored response, and saying so
        # is the truth. REAL is spelled DOUBLE PRECISION on Postgres, as in the
        # schema text.
        heartbeat_type = "DOUBLE PRECISION" if connection.dialect == "postgres" else "REAL"
        for name, kind in (("worker", "TEXT"), ("heartbeat_at", heartbeat_type),
                           ("response_json", "TEXT")):
            if name not in job_columns:
                connection.execute(f"ALTER TABLE planning_jobs ADD COLUMN {name} {kind}")
        _repoint_flight_eval_run_fk(connection)


def seed_login_user(path: Path | str, email: str, password_hash: str) -> None:
    if not email or not password_hash:
        return
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (email.lower(), password_hash),
        )
        # Repair databases created from the documented .env placeholder.
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE email = ? AND password_hash = ?",
            (password_hash, email.lower(), "replace-with-a-generated-werkzeug-password-hash"),
        )


def authenticate_user(email: str, password: str) -> sqlite3.Row | None:
    with _app_db() as db:
        user = db.execute(
            "SELECT id, name, email, country, password_hash FROM users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
    # Outside the block on purpose: the hash check is deliberately slow (scrypt),
    # and no connection should sit idle in the pool's hands while it runs.
    return user if user and check_password_hash(user["password_hash"], password) else None


def get_user_for_password_reset(email: str) -> sqlite3.Row | None:
    """Return only the authentication material needed to issue a reset token."""
    with _app_db() as db:
        return db.execute(
            "SELECT email, password_hash FROM users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()


def replace_password(email: str, expected_hash: str, new_hash: str) -> bool:
    """Atomically replace a password once, invalidating the token that authorized it."""
    with _app_db() as db:
        cursor = db.execute(
            "UPDATE users SET password_hash = ? WHERE email = ? AND password_hash = ?",
            (new_hash, email.strip().lower(), expected_hash),
        )
        return cursor.rowcount == 1


def create_user(name: str, email: str, password_hash: str, country: str,
                birthday: str) -> sqlite3.Row | None:
    """Create an account, returning None when its normalized email already exists."""
    with _app_db() as db:
        try:
            user_id = db.insert_returning_id(
                """INSERT INTO users (name, email, password_hash, country, birthday)
                   VALUES (?, ?, ?, ?, ?)""",
                (name.strip(), email.strip().lower(), password_hash, country.strip(), birthday),
            )
        except IntegrityError:
            db.rollback()
            return None
        return db.execute(
            "SELECT id, name, email, country, birthday FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


def is_database_admin(path: Path | str, email: str | None) -> bool:
    if not email:
        return False
    with connect(path) as db:
        row = db.execute(
            "SELECT is_admin FROM users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
        return bool(row and row["is_admin"])


def register_admin(path: Path | str, name: str, email: str, password_hash: str) -> dict[str, Any]:
    """Create an administrator or promote an existing account."""
    normalized = email.strip().lower()
    with connect(path) as db:
        existing = db.execute("SELECT id FROM users WHERE email = ?", (normalized,)).fetchone()
        if existing:
            db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (existing["id"],))
            user_id = existing["id"]
        else:
            user_id = db.insert_returning_id(
                """INSERT INTO users (name, email, password_hash, is_admin)
                   VALUES (?, ?, ?, 1)""",
                (name.strip(), normalized, password_hash),
            )
        row = db.execute(
            "SELECT id, name, email, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row)


def get_admins(path: Path | str) -> list[dict[str, Any]]:
    with connect(path) as db:
        return [dict(row) for row in db.execute(
            "SELECT id, name, email, created_at FROM users WHERE is_admin = 1 ORDER BY created_at DESC"
        ).fetchall()]


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def save_plan(path: Path | str, request: Any, response: Any, messages: Iterable[Any],
              user_id: int | None = None) -> None:
    """Atomically persist one completed planning workflow."""
    request_id = response.request_id
    plan = response.plan
    with connect(path) as db:
        db.execute(
            """INSERT INTO travel_requests
               (id, user_id, origin, destination, origin_city, destination_city,
                plan_scope, departure_date, return_date, travellers,
                traveller_ages_json, traveller_genders_json, budget, currency, preferences_json,
                traveller_accessibility_needs_json, accessibility_needs_json,
                refinement_notes_json, risk_tolerance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, user_id, request.origin, request.destination,
             getattr(request, "origin_city", None), getattr(request, "destination_city", None),
             getattr(request, "plan_scope", "both"), str(request.departure_date),
             str(request.return_date), request.travellers, _json(request.traveller_ages),
             _json(request.traveller_genders), request.budget, request.currency,
             _json(request.preferences), _json(request.traveller_accessibility_needs),
             _json(request.accessibility_needs), _json(request.refinement_notes),
             request.risk_tolerance),
        )
        db.execute(
            """INSERT INTO travel_plans VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (request_id, plan.title, plan.summary, _json(plan.itinerary),
             plan.estimated_total_cost, plan.currency, _json(plan.rationale),
             _json(plan.alternatives), _json(plan.sources), _json(plan.assumptions),
             _json(plan.limitations), int(plan.safety.passed), _json(plan.safety.checks),
             _json(plan.safety.warnings),),
        )
        for finding in response.agent_findings:
            finding_id = db.insert_returning_id(
                "INSERT INTO agent_findings (request_id, agent, summary, warnings_json, confidence, created_at) "
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (request_id, finding.agent, finding.summary, _json(finding.warnings), finding.confidence),
            )
            for option in finding.options:
                db.execute(
                    """INSERT INTO options
                       (finding_id, name, description, estimated_cost, currency, source_urls_json,
                        assumptions_json, limitations_json, selection_factors_json, category,
                        airport, request_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (finding_id, option.name, option.description, option.estimated_cost,
                     option.currency, _json(option.source_urls), _json(option.assumptions),
                     _json(option.limitations), _json(option.selection_factors),
                     getattr(option, "category", None),
                     getattr(option, "airport", "") or "", request_id),
                )
        for message in messages:
            item = message.model_dump(mode="json") if hasattr(message, "model_dump") else message
            db.execute(
                """INSERT INTO a2a_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT DO NOTHING""",
                (str(item["message_id"]), request_id, item["protocol_version"], item["sender"],
                 item["recipient"], item["message_type"], item["status"], str(item["created_at"]),
                 item["payload_type"], _json(item.get("payload", {})), _json(item["error"]) if item.get("error") else None),
            )


def save_audit_event(path: Path | str, item: dict[str, Any]) -> None:
    with connect(path) as db:
        db.execute(
            """INSERT INTO audit_events
               (request_id, timestamp, event, agent, details_json, previous_hash, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
            (item["request_id"], item["timestamp"], item["event"], item["agent"],
             _json(item["details"]), item["previous_hash"], item["hash"]),
        )


def create_planning_job(path: Path | str, request_id: str, user_id: int | None,
                        request_payload: Any, *, worker: str | None = None,
                        heartbeat_at: float | None = None) -> None:
    with connect(path) as db:
        db.execute(
            """INSERT INTO planning_jobs
                   (request_id, user_id, status, request_json, worker, heartbeat_at)
               VALUES (?, ?, 'queued', ?, ?, ?)
               ON CONFLICT(request_id) DO UPDATE SET status = 'queued',
                   request_json = excluded.request_json, worker = excluded.worker,
                   heartbeat_at = excluded.heartbeat_at, response_json = NULL,
                   error_type = NULL, completed_at = NULL""",
            (request_id, user_id, _json(request_payload), worker, heartbeat_at),
        )


def ensure_planning_job(path: Path | str, request_id: str, user_id: int | None,
                        request_payload: Any) -> bool:
    """Create the job row only if it is not already there. Returns True if created.

    Separate from `create_planning_job` rather than a flag on it, because the two
    want opposite things on conflict. `create_planning_job` deliberately upserts
    back to 'queued' so `jobs.submit_plan` can resubmit a request; doing that here
    would reset a job that is already running and make `/admin` report a live plan
    as queued.

    This exists for the A2A entry point, where the caller is a peer agent rather
    than the website: `agent_runs.request_id` references `planning_jobs`, so
    without a row here every `save_agent_run` on that path fails the foreign key
    and the whole request dies as an opaque error. It is idempotent because the
    parent request may already own a job — the orchestrator delegating to a
    specialist is the normal case, not an edge one.
    """
    with connect(path) as db:
        cursor = db.execute(
            """INSERT INTO planning_jobs (request_id, user_id, status, request_json)
               VALUES (?, ?, 'queued', ?)
               ON CONFLICT(request_id) DO NOTHING""",
            (request_id, user_id, _json(request_payload)),
        )
        return bool(getattr(cursor, "rowcount", 0))


def save_intake_request(path: Path | str, request_id: str, user_id: int,
                        request_payload: Any) -> bool:
    """Create or update the durable row for a conversational user request."""
    with connect(path) as db:
        existing = db.execute(
            "SELECT user_id, status FROM planning_jobs WHERE request_id = ?", (request_id,)
        ).fetchone()
        if existing and (existing["user_id"] != user_id or existing["status"] != "intake"):
            return False
        db.execute(
            """INSERT INTO planning_jobs (request_id, user_id, status, request_json)
               VALUES (?, ?, 'intake', ?)
               ON CONFLICT(request_id) DO UPDATE SET request_json = excluded.request_json""",
            (request_id, user_id, _json(request_payload)),
        )
        return True


def owns_intake_request(path: Path | str, request_id: str, user_id: int) -> bool:
    with connect(path) as db:
        row = db.execute(
            """SELECT 1 FROM planning_jobs
               WHERE request_id = ? AND user_id = ? AND status = 'intake'""",
            (request_id, user_id),
        ).fetchone()
        return row is not None


def owns_request(path: Path | str, request_id: str, user_id: int | None) -> bool:
    """Does this user own this planning request, in whatever state it is in?

    Authorization, not authentication. The API blueprint's before_request
    establishes that somebody is signed in; this answers whose request it is.

    An absent user_id is not a wildcard. A caller with no session owns nothing,
    and a request with no matching row is owned by nobody, so both answer False
    rather than falling open.
    """
    if user_id is None:
        return False
    with connect(path) as db:
        row = db.execute(
            "SELECT 1 FROM planning_jobs WHERE request_id = ? AND user_id = ?",
            (request_id, user_id),
        ).fetchone()
        return row is not None


def save_intake_message(path: Path | str, request_id: str, role: str, content: str) -> None:
    """Append one visible intake-chat turn to its parent user request."""
    if role not in {"user", "assistant"}:
        raise ValueError("Unsupported intake message role")
    with connect(path) as db:
        db.execute(
            "INSERT INTO intake_messages (request_id, role, content) VALUES (?, ?, ?)",
            (request_id, role, content),
        )


def get_intake_conversation(path: Path | str, request_id: str,
                            user_id: int | None) -> list[dict[str, Any]]:
    """Return the ordered intake thread only to its owning user."""
    if user_id is None:
        return []
    with connect(path) as db:
        return [dict(row) for row in db.execute(
            """SELECT m.role, m.content, m.created_at
               FROM intake_messages m
               JOIN planning_jobs j ON j.request_id = m.request_id
               WHERE m.request_id = ? AND j.user_id = ? ORDER BY m.id""",
            (request_id, user_id),
        ).fetchall()]


def end_user_request_session(path: Path | str, request_id: str, user_id: int) -> bool:
    with connect(path) as db:
        cursor = db.execute(
            """UPDATE planning_jobs SET session_status = 'ended',
                      session_ended_at = COALESCE(session_ended_at, CURRENT_TIMESTAMP)
               WHERE request_id = ? AND user_id = ?""",
            (request_id, user_id),
        )
        return cursor.rowcount == 1


def update_planning_job(path: Path | str, request_id: str, status: str,
                        error_type: str | None = None) -> None:
    with connect(path) as db:
        db.execute(
            """UPDATE planning_jobs SET status = ?, error_type = ?,
               completed_at = CASE WHEN ? IN ('completed', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP ELSE NULL END
               WHERE request_id = ?""",
            (status, error_type, status, request_id),
        )


# --- Plan state shared by every web pod --------------------------------------
#
# A plan's status used to live only in the memory of the pod running it, so a
# status poll that reached another pod got 404 (ADR-0005). These functions make
# `planning_jobs` the record instead. Every transition is a conditional UPDATE
# on the current status, so two pods racing (a cancel on one, completion on the
# other) cannot overwrite each other: whichever lands first wins, and the other
# sees rowcount 0.

ACTIVE_JOB_STATUSES = ("queued", "processing")
_ACTIVE = "('queued', 'processing')"


def get_planning_job(path: Path | str, request_id: str) -> dict[str, Any] | None:
    with connect(path) as db:
        row = db.execute(
            """SELECT request_id, user_id, status, error_type, worker, heartbeat_at,
                      response_json
               FROM planning_jobs WHERE request_id = ?""",
            (request_id,),
        ).fetchone()
        return dict(row) if row else None


def start_planning_job(path: Path | str, request_id: str) -> bool:
    """queued -> processing. False if it was cancelled while it waited."""
    with connect(path) as db:
        cursor = db.execute(
            "UPDATE planning_jobs SET status = 'processing' "
            "WHERE request_id = ? AND status = 'queued'",
            (request_id,),
        )
        return cursor.rowcount == 1


def finish_planning_job(path: Path | str, request_id: str, status: str, *,
                        error_type: str | None = None, response: Any = None) -> bool:
    """Record the outcome, unless the job already ended (e.g. was cancelled)."""
    with connect(path) as db:
        cursor = db.execute(
            f"""UPDATE planning_jobs SET status = ?, error_type = ?, response_json = ?,
                   completed_at = CURRENT_TIMESTAMP
                WHERE request_id = ? AND status IN {_ACTIVE}""",
            (status, error_type, _json(response) if response is not None else None,
             request_id),
        )
        return cursor.rowcount == 1


def cancel_planning_job(path: Path | str, request_id: str,
                        user_id: int | None) -> str | None:
    """Cancel the owner's job from any pod. Returns its resulting status.

    None if there is no such job or it is not this user's; an absent user_id
    owns nothing. A job that already ended keeps its status. The pod running
    the job notices on its next heartbeat.
    """
    if user_id is None:
        return None
    with connect(path) as db:
        row = db.execute(
            "SELECT user_id, status FROM planning_jobs WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None or row["user_id"] != user_id:
            return None
        cursor = db.execute(
            f"""UPDATE planning_jobs SET status = 'cancelled', completed_at = CURRENT_TIMESTAMP
                WHERE request_id = ? AND status IN {_ACTIVE}""",
            (request_id,),
        )
        if cursor.rowcount == 1:
            return "cancelled"
        return db.execute(
            "SELECT status FROM planning_jobs WHERE request_id = ?", (request_id,)
        ).fetchone()["status"]


def heartbeat_planning_jobs(path: Path | str, request_ids: Iterable[str],
                            now: float) -> set[str]:
    """Mark this pod's jobs alive; return those another pod has cancelled."""
    ids = list(request_ids)
    if not ids:
        return set()
    marks = ", ".join("?" for _ in ids)
    with connect(path) as db:
        db.execute(
            f"""UPDATE planning_jobs SET heartbeat_at = ?
                WHERE request_id IN ({marks}) AND status IN {_ACTIVE}""",
            (now, *ids),
        )
        rows = db.execute(
            f"""SELECT request_id FROM planning_jobs
                WHERE request_id IN ({marks}) AND status = 'cancelled'""",
            tuple(ids),
        ).fetchall()
        return {row["request_id"] for row in rows}


def fail_lost_planning_job(path: Path | str, request_id: str, stale_before: float) -> bool:
    """Fail a job whose pod stopped sending heartbeats (it died or was removed).

    Conditional on the heartbeat still being stale, so a job whose pod is only
    slow is never failed under it.
    """
    with connect(path) as db:
        cursor = db.execute(
            f"""UPDATE planning_jobs SET status = 'failed', error_type = 'WorkerLost',
                   completed_at = CURRENT_TIMESTAMP
                WHERE request_id = ? AND status IN {_ACTIVE}
                  AND (heartbeat_at IS NULL OR heartbeat_at < ?)""",
            (request_id, stale_before),
        )
        return cursor.rowcount == 1


def save_agent_run(path: Path | str, request_id: str, agent: str, status: str,
                   usage: dict[str, int] | None = None, response: Any = None,
                   error_type: str | None = None) -> None:
    usage = usage or {}
    response_json = _json(response.model_dump(mode="json") if hasattr(response, "model_dump") else response) if response is not None else None
    with connect(path) as db:
        db.execute(
            """INSERT INTO agent_runs (request_id, agent, status)
               VALUES (?, ?, ?) ON CONFLICT(request_id, agent) DO UPDATE SET status = excluded.status""",
            (request_id, agent, status),
        )
        if status in {"completed", "failed"}:
            db.execute(
                """UPDATE agent_runs SET status = ?, completed_at = CURRENT_TIMESTAMP,
                   input_tokens = ?, output_tokens = ?, total_tokens = ?, response_json = ?,
                   error_type = ? WHERE request_id = ? AND agent = ?""",
                (status, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                 usage.get("total_tokens", 0), response_json, error_type, request_id, agent),
            )


def save_flight_eval_run(
    path: Path | str,
    *,
    run_type: str,
    flight_agent_mode: str,
    inventory_source: str,
    outcome: str,
    latency_ms: int,
    request_id: str | None = None,
    scenario_id: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    escalated_to_loop: bool = False,
    escalation_reason: str | None = None,
    llm_turns: int = 0,
    tool_calls: int = 0,
    provider_calls: int = 0,
    usage: dict[str, int] | None = None,
    estimated_cost_usd: float | None = None,
    options_returned: int = 0,
    error_type: str | None = None,
    guardrail_input_result: str | None = None,
    guardrail_output_result: str | None = None,
    guardrail_grounding_result: str | None = None,
    guardrail_block_reason: str | None = None,
    golden_scenario_match: bool | None = None,
    golden_diff_json: Any = None,
    trace_id: str | None = None,
) -> None:
    """One row per Flight Agent run in `flight_agent_eval_runs`.

    Best-effort telemetry, not a source of truth: callers (see `flight_node`'s
    `_log_eval_run`) must swallow any exception this raises rather than let a
    logging failure turn an already-served (or already-failed) request into a
    second, unrelated failure.
    """
    usage = usage or {}
    with connect(path) as db:
        db.execute(
            """INSERT INTO flight_agent_eval_runs (
                   run_type, request_id, scenario_id, flight_agent_mode,
                   inventory_source, llm_provider, llm_model,
                   escalated_to_loop, escalation_reason, latency_ms,
                   llm_turns, tool_calls, provider_calls,
                   input_tokens, output_tokens, total_tokens, estimated_cost_usd,
                   outcome, options_returned, error_type,
                   guardrail_input_result, guardrail_output_result,
                   guardrail_grounding_result, guardrail_block_reason,
                   golden_scenario_match, golden_diff_json, trace_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_type, request_id, scenario_id, flight_agent_mode,
                inventory_source, llm_provider, llm_model,
                int(escalated_to_loop), escalation_reason, latency_ms,
                llm_turns, tool_calls, provider_calls,
                usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                usage.get("total_tokens", 0), estimated_cost_usd,
                outcome, options_returned, error_type,
                guardrail_input_result, guardrail_output_result,
                guardrail_grounding_result, guardrail_block_reason,
                None if golden_scenario_match is None else int(golden_scenario_match),
                _json(golden_diff_json) if golden_diff_json is not None else None,
                trace_id,
            ),
        )


def get_admin_activity(path: Path | str, limit: int = 50) -> list[dict[str, Any]]:
    with connect(path) as db:
        jobs = db.execute(
            """SELECT j.*, u.email, u.name, f.rating AS feedback_rating,
                      f.comment AS feedback_comment, f.updated_at AS feedback_updated_at
               FROM planning_jobs j
               LEFT JOIN users u ON u.id = j.user_id
               LEFT JOIN plan_feedback f ON f.request_id = j.request_id
               ORDER BY j.submitted_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        output = []
        for job in jobs:
            item = dict(job)
            item["display_status"] = (
                "Completed" if item["status"] in {"completed", "failed", "cancelled"}
                else "In Progress"
            )
            item["request"] = json.loads(item.pop("request_json"))
            runs = db.execute(
                "SELECT * FROM agent_runs WHERE request_id = ? ORDER BY started_at, agent",
                (job["request_id"],),
            ).fetchall()
            item["agents"] = []
            for run in runs:
                agent = dict(run)
                agent["response"] = json.loads(agent.pop("response_json")) if agent["response_json"] else None
                item["agents"].append(agent)
            item["conversation"] = [dict(message) for message in db.execute(
                """SELECT role, content, created_at FROM intake_messages
                   WHERE request_id = ? ORDER BY id""",
                (job["request_id"],),
            ).fetchall()]
            item["usage"] = {
                "input_tokens": sum(run["input_tokens"] for run in runs),
                "output_tokens": sum(run["output_tokens"] for run in runs),
                "total_tokens": sum(run["total_tokens"] for run in runs),
            }
            output.append(item)
        return output


def get_admin_token_summary(path: Path | str) -> dict[str, Any]:
    """Return cumulative token consumption, grouped by authenticated user."""
    with connect(path) as db:
        totals = db.execute(
            """SELECT COUNT(DISTINCT j.request_id) AS request_count,
                      COALESCE(SUM(r.input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(r.output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(r.total_tokens), 0) AS total_tokens
               FROM planning_jobs j
               LEFT JOIN agent_runs r ON r.request_id = j.request_id"""
        ).fetchone()
        users = db.execute(
            """SELECT j.user_id, COALESCE(u.name, u.email, 'Unknown user') AS name,
                      u.email, COUNT(DISTINCT j.request_id) AS request_count,
                      COALESCE(SUM(r.input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(r.output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(r.total_tokens), 0) AS total_tokens,
                      MAX(j.submitted_at) AS last_request_at
               FROM planning_jobs j
               LEFT JOIN users u ON u.id = j.user_id
               LEFT JOIN agent_runs r ON r.request_id = j.request_id
               GROUP BY j.user_id, u.name, u.email
               ORDER BY total_tokens DESC, last_request_at DESC"""
        ).fetchall()
        start = (date.today() - timedelta(days=30)).isoformat()
        agent_trend = [dict(row) for row in db.execute(
            """SELECT SUBSTR(j.submitted_at, 1, 10) AS date, r.agent,
                      SUM(r.input_tokens) AS input_tokens,
                      SUM(r.output_tokens) AS output_tokens,
                      SUM(r.total_tokens) AS total_tokens
               FROM agent_runs r JOIN planning_jobs j ON j.request_id = r.request_id
               WHERE SUBSTR(j.submitted_at, 1, 10) >= ?
               GROUP BY SUBSTR(j.submitted_at, 1, 10), r.agent ORDER BY date, r.agent""",
            (start,),
        ).fetchall()]
        month_start = date.today().replace(day=1)
        for _ in range(11):
            month_start = (month_start - timedelta(days=1)).replace(day=1)
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
        agent_performance = [dict(row) for row in db.execute(
            """SELECT agent, COUNT(*) AS run_count,
                      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_runs,
                      ROUND(100.0 * SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) / COUNT(*), 1) AS completion_rate,
                      ROUND(AVG(total_tokens), 1) AS average_tokens
               FROM agent_runs GROUP BY agent ORDER BY agent"""
        ).fetchall()]
        agent_totals = [dict(row) for row in db.execute(
            """SELECT agent, SUM(input_tokens) AS input_tokens,
                      SUM(output_tokens) AS output_tokens,
                      SUM(total_tokens) AS total_tokens
               FROM agent_runs GROUP BY agent ORDER BY total_tokens DESC"""
        ).fetchall()]
        return {
            "totals": dict(totals), "users": [dict(user) for user in users],
            "agent_trend": agent_trend, "agent_trend_monthly": agent_trend_monthly,
            "agent_performance": agent_performance,
            "agent_totals": agent_totals,
        }


def get_platform_dashboard(path: Path | str) -> dict[str, Any]:
    """Aggregate platform health, access, adoption, and audit-log data."""
    with connect(path) as db:
        users = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
        jobs = db.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                      COUNT(DISTINCT user_id) AS active_users
               FROM planning_jobs"""
        ).fetchone()
        total = jobs["total"] or 0
        completed = jobs["completed"] or 0
        active = jobs["active_users"] or 0
        feedback = db.execute(
            """SELECT COUNT(*) AS count,
                      SUM(CASE WHEN rating = 'up' THEN 1 ELSE 0 END) AS positive
               FROM plan_feedback"""
        ).fetchone()
        feedback_count = feedback["count"] or 0
        positive_feedback = feedback["positive"] or 0
        start = date.today() - timedelta(days=30)
        daily_jobs = {
            row["day"]: dict(row) for row in db.execute(
                """SELECT SUBSTR(submitted_at, 1, 10) AS day, COUNT(*) AS requests,
                          COUNT(DISTINCT user_id) AS active_users,
                          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
                   FROM planning_jobs WHERE SUBSTR(submitted_at, 1, 10) >= ? GROUP BY SUBSTR(submitted_at, 1, 10)""",
                (start.isoformat(),),
            ).fetchall()
        }
        registrations = {
            row["day"]: row["count"] for row in db.execute(
                """SELECT SUBSTR(created_at, 1, 10) AS day, COUNT(*) AS count FROM users
                   WHERE SUBSTR(created_at, 1, 10) >= ? GROUP BY SUBSTR(created_at, 1, 10)""",
                (start.isoformat(),),
            ).fetchall()
        }
        daily_feedback = {
            row["day"]: dict(row) for row in db.execute(
                """SELECT SUBSTR(j.submitted_at, 1, 10) AS day,
                          SUM(CASE WHEN f.rating = 'up' THEN 1 ELSE 0 END) AS positive,
                          SUM(CASE WHEN f.rating = 'down' THEN 1 ELSE 0 END) AS negative
                   FROM planning_jobs j LEFT JOIN plan_feedback f ON f.request_id = j.request_id
                   WHERE j.status = 'completed' AND SUBSTR(j.submitted_at, 1, 10) >= ?
                   GROUP BY SUBSTR(j.submitted_at, 1, 10)""",
                (start.isoformat(),),
            ).fetchall()
        }
        registered_before = db.execute(
            "SELECT COUNT(*) AS total FROM users WHERE SUBSTR(created_at, 1, 10) < ?",
            (start.isoformat(),),
        ).fetchone()["total"]
        trend = []
        running_registered = registered_before
        for offset in range(31):
            day = (start + timedelta(days=offset)).isoformat()
            running_registered += registrations.get(day, 0)
            daily = daily_jobs.get(day, {})
            daily_active = daily.get("active_users", 0) or 0
            daily_completed = daily.get("completed", 0) or 0
            feedback_day = daily_feedback.get(day, {})
            completed_day_count = daily_completed
            trend.append({
                "date": day,
                "registered_users": running_registered,
                "active_users": daily_active,
                "requests": daily.get("requests", 0) or 0,
                "completed_requests": daily_completed,
                "engagement_score": round(min(100, daily_completed * 20 / daily_active), 1) if daily_active else None,
                "good_feedback_score": round((feedback_day.get("positive", 0) or 0) * 100 / completed_day_count, 1) if completed_day_count else None,
                "bad_feedback_score": round((feedback_day.get("negative", 0) or 0) * 100 / completed_day_count, 1) if completed_day_count else None,
            })
        month_start = date.today().replace(day=1)
        for _ in range(11):
            month_start = (month_start - timedelta(days=1)).replace(day=1)
        monthly_rows = {
            row["month"]: dict(row) for row in db.execute(
                """SELECT SUBSTR(j.submitted_at, 1, 7) AS month,
                          COUNT(*) AS requests, COUNT(DISTINCT j.user_id) AS active_users,
                          SUM(CASE WHEN j.status = 'completed' THEN 1 ELSE 0 END) AS completed,
                          SUM(CASE WHEN f.rating = 'up' THEN 1 ELSE 0 END) AS positive,
                          SUM(CASE WHEN f.rating = 'down' THEN 1 ELSE 0 END) AS negative
                   FROM planning_jobs j LEFT JOIN plan_feedback f ON f.request_id = j.request_id
                   WHERE SUBSTR(j.submitted_at, 1, 10) >= ? GROUP BY SUBSTR(j.submitted_at, 1, 7)""",
                (month_start.isoformat(),),
            ).fetchall()
        }
        monthly_registrations = {
            row["month"]: row["count"] for row in db.execute(
                """SELECT SUBSTR(created_at, 1, 7) AS month, COUNT(*) AS count FROM users
                   WHERE SUBSTR(created_at, 1, 10) >= ? GROUP BY SUBSTR(created_at, 1, 7)""",
                (month_start.isoformat(),),
            ).fetchall()
        }
        monthly_registered = db.execute(
            "SELECT COUNT(*) AS total FROM users WHERE SUBSTR(created_at, 1, 10) < ?",
            (month_start.isoformat(),),
        ).fetchone()["total"]
        monthly_trend = []
        cursor = month_start
        for _ in range(12):
            month = cursor.isoformat()[:7]
            monthly_registered += monthly_registrations.get(month, 0)
            item = monthly_rows.get(month, {})
            month_active = item.get("active_users", 0) or 0
            month_completed = item.get("completed", 0) or 0
            monthly_trend.append({
                "date": month, "registered_users": monthly_registered,
                "active_users": month_active, "requests": item.get("requests", 0) or 0,
                "completed_requests": month_completed,
                "engagement_score": round(min(100, month_completed * 20 / month_active), 1) if month_active else None,
                "good_feedback_score": round((item.get("positive", 0) or 0) * 100 / month_completed, 1) if month_completed else None,
                "bad_feedback_score": round((item.get("negative", 0) or 0) * 100 / month_completed, 1) if month_completed else None,
            })
            next_month = cursor.replace(day=28) + timedelta(days=4)
            cursor = next_month.replace(day=1)
        return {
            "registered_users": users,
            "active_users": active,
            "total_requests": total,
            "completed_requests": completed,
            "failed_requests": jobs["failed"] or 0,
            "completion_rate": round(completed * 100 / total, 1) if total else 0,
            "adoption_rate": round(active * 100 / users, 1) if users else 0,
            "engagement_score": round(min(100, completed * 20 / active), 1) if active else 0,
            "feedback_count": feedback_count,
            "average_feedback": round(positive_feedback * 100 / completed, 1) if completed else None,
            "needs_improvement_rate": round((feedback_count - positive_feedback) * 100 / completed, 1) if completed else None,
            "trend": trend,
            "monthly_trend": monthly_trend,
        }


def save_plan_feedback(path: Path | str, request_id: str, user_id: int,
                       rating: str, comment: str | None) -> dict[str, Any] | None:
    """Save feedback only for the authenticated owner's completed request."""
    with connect(path) as db:
        job = db.execute(
            "SELECT request_id FROM planning_jobs WHERE request_id = ? AND user_id = ? AND status = 'completed'",
            (request_id, user_id),
        ).fetchone()
        if not job:
            return None
        db.execute(
            """INSERT INTO plan_feedback (request_id, user_id, rating, comment)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(request_id) DO UPDATE SET rating = excluded.rating,
                   comment = excluded.comment, updated_at = CURRENT_TIMESTAMP""",
            (request_id, user_id, rating, comment),
        )
        row = db.execute(
            "SELECT request_id, rating, comment, updated_at FROM plan_feedback WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return dict(row)


def get_recent_feedback(path: Path | str, limit: int = 50) -> list[dict[str, Any]]:
    with connect(path) as db:
        return [dict(row) for row in db.execute(
            """SELECT f.request_id, f.rating, f.comment, f.created_at, f.updated_at,
                      u.name, u.email, json_extract(j.request_json, '$.origin') AS origin,
                      json_extract(j.request_json, '$.destination') AS destination,
                      json_extract(j.request_json, '$.origin_city') AS origin_city,
                      json_extract(j.request_json, '$.destination_city') AS destination_city
               FROM plan_feedback f JOIN users u ON u.id = f.user_id
               JOIN planning_jobs j ON j.request_id = f.request_id
               ORDER BY f.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()]


def get_system_logs(path: Path | str, limit: int = 200) -> list[dict[str, Any]]:
    with connect(path) as db:
        rows = db.execute(
            """SELECT a.id, a.request_id, a.timestamp, a.event, a.agent, a.details_json,
                      j.status AS transaction_status, u.email, r.response_json AS agent_response_json
               FROM audit_events a
               LEFT JOIN planning_jobs j ON j.request_id = a.request_id
               LEFT JOIN users u ON u.id = j.user_id
               LEFT JOIN agent_runs r ON r.request_id = a.request_id AND r.agent = a.agent
               ORDER BY a.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            response_json = item.pop("agent_response_json")
            item["agent_response"] = json.loads(response_json) if response_json else None
            output.append(item)
        return output


@click.command("init-db")
def init_db_command() -> None:
    initialize(current_app.config["DATABASE"])
    seed_login_user(current_app.config["DATABASE"], current_app.config["LOGIN_EMAIL"],
                    current_app.config["LOGIN_PASSWORD_HASH"])
    click.echo(f"Initialized database at {current_app.config['DATABASE']}")


def init_app(app: Flask) -> None:
    app.cli.add_command(init_db_command)
    initialize(app.config["DATABASE"])
    seed_login_user(app.config["DATABASE"], app.config["LOGIN_EMAIL"],
                    app.config["LOGIN_PASSWORD_HASH"])
