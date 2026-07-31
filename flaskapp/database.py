"""SQLite persistence for users, travel plans, agent output, and audit data."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import click
from flask import Flask, current_app, g
from werkzeug.security import check_password_hash

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    country TEXT,
    birthday TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS travel_requests (
    id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
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
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1)
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
    error_type TEXT
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

CREATE INDEX IF NOT EXISTS idx_requests_user_created
    ON travel_requests(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_findings_request ON agent_findings(request_id);
CREATE INDEX IF NOT EXISTS idx_messages_request ON a2a_messages(request_id);
CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_events(request_id, id);
CREATE INDEX IF NOT EXISTS idx_jobs_submitted ON planning_jobs(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_request ON agent_runs(request_id, agent);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def initialize(path: Path | str) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        for name, definition in (
            ("name", "TEXT"), ("country", "TEXT"), ("birthday", "TEXT")
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
        request_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(travel_requests)")
        }
        for name in (
            "traveller_ages_json", "traveller_genders_json",
            "traveller_accessibility_needs_json", "refinement_notes_json",
        ):
            if name not in request_columns:
                connection.execute(
                    f"ALTER TABLE travel_requests ADD COLUMN {name} TEXT NOT NULL DEFAULT '[]'"
                )


def seed_login_user(path: Path | str, email: str, password_hash: str) -> None:
    with connect(path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO users (email, password_hash) VALUES (?, ?)",
            (email.lower(), password_hash),
        )
        # Repair databases created from the documented .env placeholder.
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE email = ? AND password_hash = ?",
            (password_hash, email.lower(), "replace-with-a-generated-werkzeug-password-hash"),
        )


def authenticate_user(email: str, password: str) -> sqlite3.Row | None:
    user = get_db().execute(
        "SELECT id, name, email, password_hash FROM users WHERE email = ?", (email.lower(),)
    ).fetchone()
    return user if user and check_password_hash(user["password_hash"], password) else None


def create_user(name: str, email: str, password_hash: str, country: str,
                birthday: str) -> sqlite3.Row | None:
    """Create an account, returning None when its normalized email already exists."""
    db = get_db()
    try:
        cursor = db.execute(
            """INSERT INTO users (name, email, password_hash, country, birthday)
               VALUES (?, ?, ?, ?, ?)""",
            (name.strip(), email.strip().lower(), password_hash, country.strip(), birthday),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return None
    return db.execute(
        "SELECT id, name, email, country, birthday FROM users WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()


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
               (id, user_id, origin, destination, departure_date, return_date, travellers,
                traveller_ages_json, traveller_genders_json, budget, currency, preferences_json,
                traveller_accessibility_needs_json, accessibility_needs_json,
                refinement_notes_json, risk_tolerance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, user_id, request.origin, request.destination, str(request.departure_date),
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
            cursor = db.execute(
                "INSERT INTO agent_findings (request_id, agent, summary, warnings_json, confidence) VALUES (?, ?, ?, ?, ?)",
                (request_id, finding.agent, finding.summary, _json(finding.warnings), finding.confidence),
            )
            for option in finding.options:
                db.execute(
                    """INSERT INTO options
                       (finding_id, name, description, estimated_cost, currency, source_urls_json,
                        assumptions_json, limitations_json, selection_factors_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cursor.lastrowid, option.name, option.description, option.estimated_cost,
                     option.currency, _json(option.source_urls), _json(option.assumptions),
                     _json(option.limitations), _json(option.selection_factors)),
                )
        for message in messages:
            item = message.model_dump(mode="json") if hasattr(message, "model_dump") else message
            db.execute(
                """INSERT OR IGNORE INTO a2a_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(item["message_id"]), request_id, item["protocol_version"], item["sender"],
                 item["recipient"], item["message_type"], item["status"], str(item["created_at"]),
                 item["payload_type"], _json(item.get("payload", {})), _json(item["error"]) if item.get("error") else None),
            )


def save_audit_event(path: Path | str, item: dict[str, Any]) -> None:
    with connect(path) as db:
        db.execute(
            """INSERT OR IGNORE INTO audit_events
               (request_id, timestamp, event, agent, details_json, previous_hash, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (item["request_id"], item["timestamp"], item["event"], item["agent"],
             _json(item["details"]), item["previous_hash"], item["hash"]),
        )


def create_planning_job(path: Path | str, request_id: str, user_id: int | None,
                        request_payload: Any) -> None:
    with connect(path) as db:
        db.execute(
            """INSERT INTO planning_jobs (request_id, user_id, status, request_json)
               VALUES (?, ?, 'queued', ?)""",
            (request_id, user_id, _json(request_payload)),
        )


def update_planning_job(path: Path | str, request_id: str, status: str,
                        error_type: str | None = None) -> None:
    with connect(path) as db:
        db.execute(
            """UPDATE planning_jobs SET status = ?, error_type = ?,
               completed_at = CASE WHEN ? IN ('completed', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP ELSE NULL END
               WHERE request_id = ?""",
            (status, error_type, status, request_id),
        )


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


def get_admin_activity(path: Path | str, limit: int = 50) -> list[dict[str, Any]]:
    with connect(path) as db:
        jobs = db.execute(
            """SELECT j.*, u.email, u.name FROM planning_jobs j
               LEFT JOIN users u ON u.id = j.user_id
               ORDER BY j.submitted_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        output = []
        for job in jobs:
            item = dict(job)
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
            output.append(item)
        return output


@click.command("init-db")
def init_db_command() -> None:
    initialize(current_app.config["DATABASE"])
    seed_login_user(current_app.config["DATABASE"], current_app.config["LOGIN_EMAIL"],
                    current_app.config["LOGIN_PASSWORD_HASH"])
    click.echo(f"Initialized database at {current_app.config['DATABASE']}")


def init_app(app: Flask) -> None:
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    initialize(app.config["DATABASE"])
    seed_login_user(app.config["DATABASE"], app.config["LOGIN_EMAIL"],
                    app.config["LOGIN_PASSWORD_HASH"])
