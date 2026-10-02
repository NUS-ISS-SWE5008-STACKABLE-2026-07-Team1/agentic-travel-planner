"""The numbers the autoscaler scales on, served on their own port.

CPU says nothing useful about this app's load: a plan spends nearly all of its
time waiting on the model, so a pod running four plans looks idle. What fills
a pod is work in flight — plans on `web` (jobs.py runs four at a time), A2A
tasks on `agents` — so each role publishes that count here, Managed Service for
Prometheus scrapes it (deploy/k8s/pod-monitoring.yaml), and the autoscalers in
deploy/k8s/hpa.yaml target an average per pod.

Its own port rather than a Flask route, because the app port is the one the
Ingress exposes; this one is not in any Service, so only something inside the
cluster that is allowed to reach the pod can read it. Hand-rolled rather than
`prometheus_client`, because two gauges do not justify a dependency in the
lockfile and the weekly pip-audit.

Off unless METRICS_PORT is set, so tests and local runs open no port.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

Gauge = tuple[str, Callable[[], float]]  # (help text, current value)

_logger = logging.getLogger(__name__)


def render(gauges: Mapping[str, Gauge]) -> str:
    """Prometheus text exposition format, one gauge per name."""
    lines = []
    for name, (help_text, read) in gauges.items():
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", f"{name} {float(read())}"]
    return "\n".join(lines) + "\n"


def start_metrics_server(gauges: Mapping[str, Gauge], port: int | None = None
                         ) -> ThreadingHTTPServer | None:
    """Serve `/metrics` on a daemon thread. None when no port is configured."""
    if port is None:
        configured = os.getenv("METRICS_PORT", "").strip()
        if not configured:
            return None
        port = int(configured)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — the stdlib's name
            if self.path.split("?")[0] != "/metrics":
                self.send_error(404)
                return
            body = render(gauges).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # one scrape every 30 s is not news
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # nosec B104 — in-cluster only, see above
    threading.Thread(target=server.serve_forever, name="metrics", daemon=True).start()
    _logger.info("Metrics on :%s/metrics: %s", port, ", ".join(gauges))
    return server
