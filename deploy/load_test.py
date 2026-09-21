"""Submit several plans at once and check none of them gets lost.

Two different jobs, one script:

* **Today**, against a single web pod, it establishes the baseline: N plans
  really do run concurrently inside one process, every status poll finds its
  job, and here is the latency spread.
* **After job state moves into `planning_jobs`**, the same run against 2+
  replicas is the proof that the fix worked. The failure it hunts for is
  specific: a status poll answered by a pod that never ran the job replies 404
  while the plan is running perfectly in its sibling (ADR-0005). So every 404
  is counted and reported, not just the final outcome.

Each plan gets its own signed-in session, as separate travellers would.

    # local, free, deterministic (deploy/docker-compose.yml + the CI stub model)
    python deploy/load_test.py --base-url http://127.0.0.1:5000 --plans 2

    # against the cluster; this one spends real tokens, N plans' worth
    kubectl port-forward -n travel-planner svc/web 5000:80
    python deploy/load_test.py --base-url http://127.0.0.1:5000 --plans 2
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

# Same trip as the smoke test: a stocked seed route with an accessibility need,
# so all four specialists run.
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


@dataclass
class Result:
    index: int
    request_id: str | None = None
    status: str = "not submitted"
    submit_seconds: float = 0.0
    total_seconds: float = 0.0
    polls: int = 0
    not_found: int = 0          # the ADR-0005 failure: 404 for a live job
    errors: list[str] = field(default_factory=list)


class _PortForwardSession(requests.Session):
    """A session that treats the local port-forward hop as secure.

    The cluster sets SESSION_COOKIE_SECURE, and `requests` (unlike a browser)
    will not send a Secure cookie over plain HTTP even to localhost, so every
    call arrives without a session.

    Relaxing the jar has to happen while the request is being PREPARED. Doing it
    from a response hook does not work: requests dispatches response hooks
    *before* it stores new cookies, so each fresh Secure cookie lands right
    after the hook has run, and the next request is unauthenticated again.
    """

    def prepare_request(self, request):
        for cookie in self.cookies:
            cookie.secure = False
        return super().prepare_request(request)


def _new_session(base: str) -> requests.Session:
    local = base.startswith(("http://127.0.0.1", "http://localhost"))
    return _PortForwardSession() if local else requests.Session()


def _session(base: str, email: str, password: str) -> tuple[requests.Session, str]:
    http = _new_session(base)
    page = http.get(f"{base}/", timeout=30)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
    if not token:
        raise RuntimeError(f"no CSRF token on the login page (HTTP {page.status_code})")
    # allow_redirects=False is load-bearing. The POST answers 302 -> /main, and
    # if requests chases it the just-issued session cookie is still marked
    # Secure, so it is not sent, /main bounces back to the login page, and that
    # response REPLACES the authenticated cookie with a fresh anonymous one.
    http.post(f"{base}/", data={
        "csrf_token": token.group(1), "email": email, "password": password,
    }, timeout=30, allow_redirects=False)
    main = http.get(f"{base}/main", timeout=30, allow_redirects=False)
    api_token = re.search(r'name="csrf-token" content="([^"]+)"', main.text)
    if main.status_code != 200 or not api_token:
        raise RuntimeError(f"login failed (GET /main -> {main.status_code})")
    return http, api_token.group(1)


def run_one(index: int, base: str, email: str, password: str, timeout: int,
            start_together: threading.Barrier) -> Result:
    result = Result(index=index)
    try:
        http, csrf = _session(base, email, password)
    except Exception as exc:  # noqa: BLE001 - reported per plan, never fatal
        result.errors.append(f"login: {exc}")
        return result

    start_together.wait()  # submit at the same moment, not staggered by login
    started = time.monotonic()
    try:
        submitted = http.post(
            f"{base}/api/v1/travel-plans", json=PLAN_REQUEST,
            headers={"X-CSRFToken": csrf}, timeout=90,
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"submit: {exc}")
        return result
    result.submit_seconds = time.monotonic() - started
    if submitted.status_code != 202:
        result.errors.append(f"submit -> {submitted.status_code}: {submitted.text[:200]}")
        return result
    result.request_id = submitted.json()["request_id"]
    result.status = "processing"

    while True:
        if time.monotonic() - started > timeout:
            result.status = "timed out"
            break
        time.sleep(2)
        result.polls += 1
        try:
            response = http.get(
                f"{base}/api/v1/travel-plans/{result.request_id}/status", timeout=30
            )
        except requests.ConnectionError as exc:
            # A dropped port-forward connection is not a lost job.
            result.errors.append(f"poll: {type(exc).__name__}")
            continue
        if response.status_code == 404:
            # THE failure this test exists for.
            result.not_found += 1
            continue
        if response.status_code != 200:
            result.errors.append(f"poll -> {response.status_code}")
            continue
        state = response.json().get("status", "?")
        if state in {"completed", "failed", "cancelled"}:
            result.status = state
            break
    result.total_seconds = time.monotonic() - started
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--plans", type=int, default=2)
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="TravelDemo2026!")
    parser.add_argument("--timeout", type=int, default=600, help="per plan, seconds")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print(f"submitting {args.plans} plans at once against {base}\n")
    barrier = threading.Barrier(args.plans)
    wall_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.plans) as pool:
        results = list(pool.map(
            lambda i: run_one(i, base, args.email, args.password, args.timeout, barrier),
            range(args.plans),
        ))
    wall = time.monotonic() - wall_start

    print(f"{'plan':<6}{'status':<12}{'submit':>8}{'total':>9}{'polls':>7}{'404s':>6}  request_id")
    for r in sorted(results, key=lambda r: r.index):
        print(f"{r.index:<6}{r.status:<12}{r.submit_seconds:>7.1f}s{r.total_seconds:>8.1f}s"
              f"{r.polls:>7}{r.not_found:>6}  {r.request_id or '-'}")
        for error in r.errors:
            print(f"        ! {error}")

    completed = [r for r in results if r.status == "completed"]
    latencies = sorted(r.total_seconds for r in completed)
    lost = sum(r.not_found for r in results)
    print(f"\ncompleted      {len(completed)}/{len(results)}")
    if latencies:
        p95 = latencies[min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))]
        print(f"latency        min {latencies[0]:.1f}s | median "
              f"{statistics.median(latencies):.1f}s | p95 {p95:.1f}s | max {latencies[-1]:.1f}s")
        print(f"submit p95     {sorted(r.submit_seconds for r in completed)[-1]:.1f}s")
    print(f"wall clock     {wall:.1f}s for all {len(results)}")
    print(f"404 on status  {lost}   <- must be 0; anything else is ADR-0005 biting")

    ok = len(completed) == len(results) and lost == 0
    print("\nPASS" if ok else "\nFAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
