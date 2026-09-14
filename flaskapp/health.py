"""Liveness and readiness probes for a container orchestrator.

Two endpoints rather than one, because the two questions have opposite
consequences when the answer is no:

- **Liveness** (`/healthz`) failing makes Kubernetes *restart* the container.
  It must therefore depend on nothing outside this process. If it checked the
  database, a Supabase blip would restart every pod at once — killing the
  planning jobs held in `jobs.py`'s in-memory dicts — and the restarted pods
  would find the database exactly as unreachable as before.
- **Readiness** (`/readyz`) failing only takes the pod *out of the load
  balancer* until it recovers. That is the right response to a dependency
  being down, so this one does check the database.

Both are unauthenticated on purpose: the kubelet and the load balancer's
health checker carry no session. Neither returns anything a stranger could
use — a status word, never an error message or a DSN.

`/` is not used for either. It renders the login page through Jinja and the
CSRF machinery, which is slower and can fail for reasons unrelated to whether
the process can serve traffic.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from flaskapp.database import connect

health_bp = Blueprint("health", __name__)


@health_bp.get("/healthz")
def healthz():
    """The process is up and answering HTTP. Nothing else."""
    return jsonify(status="ok")


@health_bp.get("/readyz")
def readyz():
    """The process can reach its database and so can do useful work."""
    try:
        with connect(current_app.config["DATABASE"]) as connection:
            connection.execute("SELECT 1").fetchone()
    except Exception as exc:
        # The exception class only. psycopg's message can echo the host, and
        # CONNECTION_HELP is appended to it — neither belongs in a probe log
        # that runs every few seconds.
        current_app.logger.warning("Readiness check failed: %s", type(exc).__name__)
        return jsonify(status="unavailable"), 503
    return jsonify(status="ok")
