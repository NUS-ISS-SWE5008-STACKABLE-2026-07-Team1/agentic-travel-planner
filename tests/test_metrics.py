"""The work-in-flight gauges the autoscalers scale on (flaskapp/metrics.py)."""

from __future__ import annotations

import asyncio
import threading
import urllib.error
import urllib.request

import pytest

from flaskapp.metrics import render, start_metrics_server
from flaskapp.travel_ai import jobs
from flaskapp.travel_ai.a2a_standard import _CountCallsInFlight, calls_in_flight


def test_render_is_prometheus_text_format():
    text = render({"plans": ("Plans in flight", lambda: 3)})

    assert text == "# HELP plans Plans in flight\n# TYPE plans gauge\nplans 3.0\n"


def test_no_port_means_no_server(monkeypatch):
    monkeypatch.delenv("METRICS_PORT", raising=False)

    assert start_metrics_server({"x": ("x", lambda: 1)}) is None


def test_the_server_answers_metrics_and_nothing_else():
    count = {"value": 2}
    server = start_metrics_server({"plans": ("p", lambda: count["value"])}, port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert "plans 2.0" in urllib.request.urlopen(f"{base}/metrics").read().decode()
        count["value"] = 5  # read live on every scrape, not cached
        assert "plans 5.0" in urllib.request.urlopen(f"{base}/metrics").read().decode()
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/")
        assert error.value.code == 404
    finally:
        server.shutdown()


def test_plans_in_flight_counts_this_pods_running_and_queued_plans():
    before = jobs.plans_in_flight()
    jobs._running["gauge-a"] = jobs._LocalRun("db", threading.Event())
    jobs._running["gauge-b"] = jobs._LocalRun("db", threading.Event())
    try:
        assert jobs.plans_in_flight() == before + 2
    finally:
        jobs._running.pop("gauge-a")
        jobs._running.pop("gauge-b")


def test_calls_in_flight_rises_during_a_call_and_falls_after():
    seen = {}

    async def slow_app(scope, receive, send):
        seen["during"] = calls_in_flight()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def nothing():
        return {"type": "http.request"}

    async def discard(_message):
        pass

    counted = _CountCallsInFlight(slow_app)
    before = calls_in_flight()
    asyncio.run(counted({"type": "http", "method": "POST"}, nothing, discard))
    asyncio.run(counted({"type": "http", "method": "GET"}, nothing, discard))

    assert seen["during"] == before  # the GET (agent card) is not work
    asyncio.run(counted({"type": "http", "method": "POST"}, nothing, discard))
    assert seen["during"] == before + 1
    assert calls_in_flight() == before


def test_a_failed_call_still_comes_off_the_count():
    async def broken(scope, receive, send):
        raise RuntimeError("agent crashed")

    before = calls_in_flight()
    with pytest.raises(RuntimeError):
        asyncio.run(_CountCallsInFlight(broken)({"type": "http", "method": "POST"}, None, None))

    assert calls_in_flight() == before


def test_a_stopping_worker_finishes_running_and_queued_plans(tmp_path):
    """The drain rests on this: when gunicorn stops a worker it returns from its
    loop and exits normally, and a normal exit waits for jobs.py's executor —
    the plan running AND the ones queued behind it. Proven in a real separate
    process, with more plans than MAX_CONCURRENT_PLANS so one has to queue."""
    import subprocess
    import sys
    from pathlib import Path

    script = f"""
import sys, time
from pathlib import Path
from flaskapp.travel_ai import jobs
out = Path({str(tmp_path)!r})
def plan(n):
    time.sleep(0.5)
    (out / f"plan-{{n}}").write_text("done")
for n in range(jobs.MAX_CONCURRENT_PLANS + 1):
    jobs._executor.submit(plan, n)
sys.exit(0)
"""
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-c", script], cwd=root, check=True, timeout=60)

    finished = sorted(p.name for p in tmp_path.glob("plan-*"))
    assert len(finished) == jobs.MAX_CONCURRENT_PLANS + 1, finished
