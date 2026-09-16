"""Prove one plan crossed the A2A boundary between the web and agents roles.

Not a quality test — scripts/e2e_test.py is that, and it asserts grounding
against a real model. This asks only the deployment question: did every
specialist the orchestrator dispatched answer *over the network*, from a
different container, with nothing falling back to an in-process call?

The evidence is the web role's own audit trace. `a2a_client.py` records
`a2a_call_completed` for each remote specialist and `a2a_transport_failed`
when one cannot be reached, and it raises rather than silently running the
agent locally — so a completed plan with a completion event per dispatched
specialist is not something an in-process run can produce.

Against the local compose stack this costs nothing (the model is a stub).
Against a cluster configured with a real provider key, it spends one plan's
worth of tokens.

    python deploy/smoke_test.py --base-url http://127.0.0.1:5000
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import requests

# Inside the seed inventory's window (2026-08-24 to 2027-01-06) on a stocked
# route, so the flight agent takes its normal path rather than Path 2.
# A stated accessibility need makes dispatch include the accessibility agent,
# so all four specialists are exercised. Cities are set because the browser
# form always sends them, and Risk & Advisory resolves its reference data from
# the city: without one, the first GKE run fell back to prompt-only for Risk.
PLAN_REQUEST = {
    "origin": "Singapore", "destination": "Japan", "plan_scope": "both",
    "origin_city": "Singapore", "destination_city": "Tokyo",
    "departure_date": "2026-11-10", "return_date": "2026-11-15",
    "travellers": 1, "traveller_ages": [34], "traveller_genders": ["prefer_not_to_say"],
    "traveller_accessibility_needs": [["wheelchair assistance"]],
    "budget": 4000, "currency": "SGD",
    "preferences": [], "accessibility_needs": ["wheelchair assistance"],
    "refinement_notes": [],
}


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    sys.exit(1)


def get_json(http: requests.Session, url: str, attempts: int = 5) -> dict:
    """A read that survives a dropped connection.

    `kubectl port-forward` on Windows occasionally kills one reused connection
    ("An existing connection was forcibly closed by the remote host") while the
    pod is perfectly healthy. The first run against GKE died that way mid-poll,
    with the plan still running. Only safe because every call here is a GET.
    """
    for attempt in range(1, attempts + 1):
        try:
            return http.get(url, timeout=30).json()
        except requests.ConnectionError:
            if attempt == attempts:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def login(base: str, email: str, password: str) -> tuple[requests.Session, str]:
    http = requests.Session()
    page = http.get(f"{base}/", timeout=30)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
    if not token:
        fail(f"no CSRF token on the login page (HTTP {page.status_code})")
    http.post(f"{base}/", data={
        "csrf_token": token.group(1), "email": email, "password": password,
    }, timeout=30)
    # The login form's token is bound to the pre-login session; the API needs
    # the one rendered on an authenticated page.
    if base.startswith(("http://127.0.0.1", "http://localhost")):
        # SESSION_COOKIE_SECURE is on in the cluster, and requests (unlike a
        # browser) never returns a Secure cookie over plain HTTP, even to
        # localhost. Through `kubectl port-forward` the hop is local, so relax it.
        for cookie in http.cookies:
            cookie.secure = False
    main = http.get(f"{base}/main", timeout=30, allow_redirects=False)
    api_token = re.search(r'name="csrf-token" content="([^"]+)"', main.text)
    if main.status_code != 200 or not api_token:
        fail(f"login did not produce a session (GET /main -> {main.status_code})")
    print("PASS  signed in")
    return http, api_token.group(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="TravelDemo2026!")
    parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for the plan")
    parser.add_argument("--request-id", help="resume waiting on an already-submitted plan")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    for probe in ("/healthz", "/readyz"):
        code = requests.get(f"{base}{probe}", timeout=10).status_code
        if code != 200:
            fail(f"{probe} -> {code}")
    print("PASS  /healthz and /readyz")

    http, api_token = login(base, args.email, args.password)
    if args.request_id:
        # Resuming never resubmits: a second POST would pay for a second plan.
        request_id = args.request_id
        print(f"INFO  resuming request_id={request_id} (no new plan submitted)")
    else:
        submitted = http.post(
            f"{base}/api/v1/travel-plans", json=PLAN_REQUEST,
            headers={"X-CSRFToken": api_token}, timeout=60,
        )
        if submitted.status_code != 202:
            fail(f"plan submission -> {submitted.status_code}: {submitted.text[:300]}")
        request_id = submitted.json()["request_id"]
        print(f"PASS  plan accepted, request_id={request_id}")

    started = time.monotonic()
    while True:
        status = get_json(http, f"{base}/api/v1/travel-plans/{request_id}/status")
        if status["status"] in {"completed", "failed", "cancelled"}:
            break
        if time.monotonic() - started > args.timeout:
            fail(f"plan still {status['status']} after {args.timeout}s")
        time.sleep(2)
    elapsed = time.monotonic() - started
    if status["status"] != "completed":
        fail(f"plan ended {status['status']}: {status.get('error')}")
    print(f"PASS  plan completed in {elapsed:.0f}s")

    events = get_json(http, f"{base}/api/v1/traces/{request_id}").get("events", [])
    dispatched = next(
        (e["details"]["dispatched"] for e in events if e.get("event") == "specialists_dispatched"),
        None,
    )
    if not dispatched:
        fail("trace has no specialists_dispatched event")
    failed = sorted({e["agent"] for e in events if e.get("event") == "a2a_transport_failed"})
    if failed:
        fail(f"A2A transport failed for: {', '.join(failed)}")
    remote = {e["agent"] for e in events if e.get("event") == "a2a_call_completed"}
    missing = sorted(set(dispatched) - remote)
    if missing:
        fail(
            f"no a2a_call_completed for {', '.join(missing)} — those ran in-process, "
            "so A2A_INTERNAL_ENABLED is not reaching the web role"
        )
    for name in dispatched:
        took = next(e["details"]["elapsed_ms"] for e in events
                    if e.get("event") == "a2a_call_completed" and e["agent"] == name)
        print(f"PASS  {name} answered over A2A in {took} ms")
    print(f"\nOK    {len(dispatched)} specialist(s) crossed the network boundary")


if __name__ == "__main__":
    main()
