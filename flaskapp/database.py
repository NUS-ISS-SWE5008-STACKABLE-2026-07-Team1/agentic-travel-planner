"""SQLite persistence for users, travel plans, agent output, and audit data."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
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
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS travel_requests (
    id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    -- Countries above, cities here. NULLable because city intake postdates
    -- these rows and a country-only request stays valid.
    origin_city TEXT,
    destination_city TEXT,
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
    error_type TEXT,
    session_status TEXT NOT NULL DEFAULT 'active',
    session_ended_at TEXT
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
            ("name", "TEXT"), ("country", "TEXT"), ("birthday", "TEXT"),
            ("is_admin", "INTEGER NOT NULL DEFAULT 0")
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
        # Cities are NULLable, unlike the country columns: rows written before
        # city intake existed have no city and must stay readable. A NULL here
        # means "country granularity", which the flight adapter handles by
        # falling back to the country's main gateway.
        for name in ("origin_city", "destination_city"):
            if name not in request_columns:
                connection.execute(f"ALTER TABLE travel_requests ADD COLUMN {name} TEXT")
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(planning_jobs)")}
        if "session_status" not in job_columns:
            connection.execute(
                "ALTER TABLE planning_jobs ADD COLUMN session_status TEXT NOT NULL DEFAULT 'active'"
            )
        if "session_ended_at" not in job_columns:
            connection.execute("ALTER TABLE planning_jobs ADD COLUMN session_ended_at TEXT")


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
        "SELECT id, name, email, country, password_hash FROM users WHERE email = ?", (email.lower(),)
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
            cursor = db.execute(
                """INSERT INTO users (name, email, password_hash, is_admin)
                   VALUES (?, ?, ?, 1)""",
                (name.strip(), normalized, password_hash),
            )
            user_id = cursor.lastrowid
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
                departure_date, return_date, travellers,
                traveller_ages_json, traveller_genders_json, budget, currency, preferences_json,
                traveller_accessibility_needs_json, accessibility_needs_json,
                refinement_notes_json, risk_tolerance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, user_id, request.origin, request.destination,
             getattr(request, "origin_city", None), getattr(request, "destination_city", None),
             str(request.departure_date),
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
               VALUES (?, ?, 'queued', ?)
               ON CONFLICT(request_id) DO UPDATE SET status = 'queued',
                   request_json = excluded.request_json""",
            (request_id, user_id, _json(request_payload)),
        )


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
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
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
            "SELECT COUNT(*) FROM users WHERE SUBSTR(created_at, 1, 10) < ?", (start.isoformat(),)
        ).fetchone()[0]
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
            "SELECT COUNT(*) FROM users WHERE SUBSTR(created_at, 1, 10) < ?", (month_start.isoformat(),)
        ).fetchone()[0]
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
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    initialize(app.config["DATABASE"])
    seed_login_user(app.config["DATABASE"], app.config["LOGIN_EMAIL"],
                    app.config["LOGIN_PASSWORD_HASH"])
