"""Prove the deployment is really two containers, and say honestly how far it replicates.

`smoke_test.py` proves a plan crossed the network. This proves the *shape* that
made it cross: two Deployments, one image, two commands, two pods, two Services,
and a NetworkPolicy that stops anything but `web` reaching `agents`. All of it
reads the cluster — no plan is submitted, so it costs nothing to run.

    python deploy/prove_topology.py

Three optional experiments, in rising order of disruption. Each says what it
costs before it runs:

    --restart-agents        restart `agents` only; show `web` never moved
    --scale web=2           scale, wait, count Service endpoints, then restore
    --session-portability   with `web` at 2: sign in through one pod, then use
                            that same session against the OTHER pod

The last one is the interesting one, because it separates two claims people
merge. Authentication is a signed cookie, so it is portable across pods *today*.
Job state is a module-level dict, so it is not (ADR-0005). A deployment can be
half-replicable, and this is how you show which half.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

import requests

NAMESPACE = "travel-planner"

_passes = 0
_failures: list[str] = []


def ok(message: str) -> None:
    global _passes
    _passes += 1
    print(f"PASS  {message}")


def bad(message: str) -> None:
    _failures.append(message)
    print(f"FAIL  {message}")


def note(message: str) -> None:
    print(f"      {message}")


def kubectl(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["kubectl", "-n", NAMESPACE, *args],
        capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise SystemExit(f"kubectl {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def get(kind: str, name: str = "") -> dict:
    return json.loads(kubectl("get", kind, *( [name] if name else [] ), "-o", "json"))


# --------------------------------------------------------------------------
# 1. Containerised, not monolithic
# --------------------------------------------------------------------------

def prove_two_roles() -> None:
    print("\n--- one image, two roles -------------------------------------")
    web = get("deployment", "web")
    agents = get("deployment", "agents")
    web_c = web["spec"]["template"]["spec"]["containers"][0]
    agents_c = agents["spec"]["template"]["spec"]["containers"][0]

    if web_c["image"] == agents_c["image"]:
        ok(f"both roles run the SAME image: {web_c['image']}")
        note("so the split is a runtime decision, not two codebases drifting apart")
    else:
        bad(f"images differ: {web_c['image']} vs {agents_c['image']}")

    web_cmd = " ".join(web_c.get("command") or ["<image CMD: gunicorn>"])
    agents_cmd = " ".join(agents_c.get("command") or ["<image CMD>"])
    if web_cmd != agents_cmd:
        ok("the two roles run DIFFERENT commands")
        note(f"web    -> {web_cmd}")
        note(f"agents -> {agents_cmd}")
    else:
        bad("both roles run the same command — that is one role twice")

    # Different probe paths are a second, independent signal that these are two
    # different servers: one answers Flask's /healthz, the other an A2A card.
    web_probe = web_c["livenessProbe"]["httpGet"]["path"]
    agents_probe = agents_c["livenessProbe"]["httpGet"]["path"]
    if web_probe != agents_probe:
        ok("different liveness endpoints — two different HTTP servers")
        note(f"web    -> {web_probe}   (Flask)")
        note(f"agents -> {agents_probe}   (A2A agent card)")
    else:
        bad("both probe the same path")


def prove_separate_pods() -> None:
    print("\n--- separate processes, separate machines --------------------")
    pods = get("pods")["items"]
    running = {}
    for pod in pods:
        app = pod["metadata"]["labels"].get("app")
        if app in {"web", "agents"} and pod["status"]["phase"] == "Running":
            running.setdefault(app, []).append(pod)

    for role in ("web", "agents"):
        if not running.get(role):
            bad(f"no Running pod for {role}")
            return

    ips = {role: [p["status"]["podIP"] for p in pods_] for role, pods_ in running.items()}
    if set(ips["web"]).isdisjoint(ips["agents"]):
        ok("separate pod IPs — the A2A hop is a real network call")
        note(f"web    {', '.join(ips['web'])}")
        note(f"agents {', '.join(ips['agents'])}")
    else:
        bad("the roles share an IP")

    # Resource accounting is per pod. In a monolith there is one number.
    for role, pods_ in sorted(running.items()):
        req = pods_[0]["spec"]["containers"][0]["resources"]["requests"]
        note(f"{role:<7} {len(pods_)} pod(s), each requesting "
             f"{req['cpu']} CPU / {req['memory']} — billed and scheduled separately")

    usage = kubectl("top", "pods", "--no-headers", check=False).strip()
    if usage:
        ok("live per-pod resource usage (a monolith reports one figure)")
        for line in usage.splitlines():
            note(line.strip())


def prove_isolation() -> None:
    print("\n--- the boundary is enforced, not just drawn -----------------")
    ingress = get("ingress")["items"]
    backends = set()
    for ing in ingress:
        default = ing["spec"].get("defaultBackend", {}).get("service", {}).get("name")
        if default:
            backends.add(default)
        for rule in ing["spec"].get("rules", []):
            for path in rule.get("http", {}).get("paths", []):
                backends.add(path["backend"]["service"]["name"])
    if backends == {"web"}:
        ok("the public load balancer routes to `web` only — `agents` has no public door")
    else:
        bad(f"Ingress backends are {sorted(backends) or 'none'}; expected just web")

    policies = get("networkpolicy")["items"]
    guarding = [
        p for p in policies
        if p["spec"].get("podSelector", {}).get("matchLabels", {}).get("app") == "agents"
    ]
    if not guarding:
        bad("no NetworkPolicy selects app=agents")
        return
    allowed = set()
    ports = set()
    for rule in guarding[0]["spec"].get("ingress", []):
        for source in rule.get("from", []):
            label = source.get("podSelector", {}).get("matchLabels", {}).get("app")
            if label:
                allowed.add(label)
        for port in rule.get("ports", []):
            ports.add(port["port"])
    if allowed == {"web"}:
        ok(f"NetworkPolicy admits app=web only, on port {sorted(ports)[0] if ports else '?'}")
        note("the A2A endpoints have no authentication of their own; this substitutes")
    else:
        bad(f"NetworkPolicy admits {sorted(allowed)}; expected just web")


def prove_wiring() -> None:
    print("\n--- the web role is configured to go over the wire -----------")
    config = get("configmap", "app-config")["data"]
    base = config.get("A2A_BASE_URL", "")
    match = re.match(r"https?://([^:/]+)", base)
    if match and match.group(1) == "agents":
        ok(f"A2A_BASE_URL={base} — resolved by Kubernetes Service DNS, not localhost")
    else:
        bad(f"A2A_BASE_URL={base!r} does not point at the agents Service")

    web = get("deployment", "web")["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in web.get("env", [])}
    if env.get("A2A_INTERNAL_ENABLED") == "true":
        ok("A2A_INTERNAL_ENABLED=true — all four specialists are dispatched remotely")
        note("set it to false and the same image runs them in-process: that is the")
        note("counterfactual, and `deploy/smoke_test.py` is what tells the two apart")
    else:
        bad("A2A_INTERNAL_ENABLED is not true — specialists would run in-process")


# --------------------------------------------------------------------------
# 2. Experiments
# --------------------------------------------------------------------------

def restart_agents() -> None:
    print("\n--- independent lifecycle ------------------------------------")
    print("      restarting `agents`; `web` should not notice. ~60s.")
    before = get("pods")["items"]
    web_before = {p["metadata"]["uid"] for p in before
                  if p["metadata"]["labels"].get("app") == "web"}
    kubectl("rollout", "restart", "deployment/agents")
    kubectl("rollout", "status", "deployment/agents", "--timeout=180s")
    after = get("pods")["items"]
    web_after = {p["metadata"]["uid"] for p in after
                 if p["metadata"]["labels"].get("app") == "web"
                 and p["status"]["phase"] == "Running"}
    agents_after = {p["metadata"]["uid"] for p in after
                    if p["metadata"]["labels"].get("app") == "agents"
                    and p["status"]["phase"] == "Running"}
    if web_before and web_before == web_after:
        ok("`web` pods are the same instances — untouched by the agents restart")
    else:
        bad("`web` pods changed during an agents-only restart")
    if agents_after and not (agents_after & {p["metadata"]["uid"] for p in before}):
        ok("`agents` pods were genuinely replaced")
    else:
        bad("agents pods did not change")
    note("a monolith cannot do this: restarting the agents restarts the website")


def scale(role: str, replicas: int) -> int:
    previous = get("deployment", role)["spec"]["replicas"]
    kubectl("scale", f"deployment/{role}", f"--replicas={replicas}")
    kubectl("rollout", "status", f"deployment/{role}", "--timeout=300s", check=False)
    return previous


def endpoint_ips(role: str) -> list[str]:
    slices = get("endpointslice")["items"]
    ips: list[str] = []
    for slice_ in slices:
        if slice_["metadata"]["labels"].get("kubernetes.io/service-name") != role:
            continue
        for endpoint in slice_.get("endpoints", []):
            if endpoint.get("conditions", {}).get("ready"):
                ips.extend(endpoint["addresses"])
    return sorted(ips)


def prove_scale(role: str, replicas: int, restore: bool) -> list[str]:
    print(f"\n--- scaling {role} to {replicas} ------------------------------------")
    previous = scale(role, replicas)
    deadline = time.monotonic() + 300
    ips: list[str] = []
    while time.monotonic() < deadline:
        ips = endpoint_ips(role)
        if len(ips) >= replicas:
            break
        time.sleep(5)
    if len(ips) == replicas:
        ok(f"{replicas} ready endpoints behind Service/{role}: {', '.join(ips)}")
        note("the platform schedules, health-checks and load-balances them. That is")
        note("a real result, and it is NOT the same as the application working at 2.")
    else:
        bad(f"expected {replicas} ready endpoints, saw {len(ips)}: {ips}")
    if restore:
        note(f"restoring {role} to {previous}")
        scale(role, previous)
    return ips


def session_portability(replicas: int, email: str, password: str) -> None:
    """Sign in through one pod; use that session against another.

    This is the honest half of the scaling story. Flask signs the session into a
    cookie, so any pod with the same SECRET_KEY accepts it — no sticky sessions,
    no shared session store. Job state is the part that does not travel.
    """
    print("\n--- what actually survives a second pod ----------------------")
    pods = [p["metadata"]["name"] for p in get("pods")["items"]
            if p["metadata"]["labels"].get("app") == "web"
            and p["status"]["phase"] == "Running"]
    if len(pods) < 2:
        bad(f"need 2 running web pods, found {len(pods)} — run with --scale web=2")
        return

    forwards = []
    try:
        for index, pod in enumerate(pods[:2]):
            port = 5100 + index
            forwards.append((port, pod, subprocess.Popen(
                ["kubectl", "-n", NAMESPACE, "port-forward", f"pod/{pod}", f"{port}:8000"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )))
        time.sleep(4)

        http = _LocalSession()
        first_port, first_pod, _ = forwards[0]
        second_port, second_pod, _ = forwards[1]
        base_one = f"http://127.0.0.1:{first_port}"
        base_two = f"http://127.0.0.1:{second_port}"

        page = http.get(f"{base_one}/", timeout=30)
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
        if not token:
            bad(f"no login form from {first_pod} (HTTP {page.status_code})")
            return
        http.post(f"{base_one}/", data={
            "csrf_token": token.group(1), "email": email, "password": password,
        }, timeout=30, allow_redirects=False)
        landed = http.get(f"{base_one}/main", timeout=30, allow_redirects=False)
        if landed.status_code != 200:
            bad(f"login through {first_pod} failed (GET /main -> {landed.status_code})")
            return
        ok(f"signed in through pod {first_pod}")

        crossed = http.get(f"{base_two}/main", timeout=30, allow_redirects=False)
        if crossed.status_code == 200:
            ok(f"the SAME session is accepted by pod {second_pod}")
            note("authentication is a signed cookie, so it needs no sticky sessions,")
            note("no shared session store, and no work to replicate. This half scales.")
        else:
            bad(f"pod {second_pod} rejected the session (HTTP {crossed.status_code})")

        # And now the half that does not.
        missing = http.get(f"{base_two}/api/v1/travel-plans/{'0' * 8}-probe/status",
                           timeout=30)
        note(f"status of an unknown plan on pod two -> HTTP {missing.status_code} "
             "(expected: 404)")
        note("that 404 is harmless for a made-up id. The defect is that a plan RUNNING")
        note("on pod one answers exactly the same way here, because _jobs is a dict in")
        note("pod one's memory. `deploy/load_test.py --plans 2` is what measures it.")
    finally:
        for _, _, process in forwards:
            process.terminate()


class _LocalSession(requests.Session):
    """The cluster marks the session cookie Secure; port-forward is plain HTTP."""

    def prepare_request(self, request):
        for cookie in self.cookies:
            cookie.secure = False
        return super().prepare_request(request)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--restart-agents", action="store_true",
                        help="restart the agents role only (~60s, brief A2A outage)")
    parser.add_argument("--scale", metavar="ROLE=N",
                        help="scale a role, count ready endpoints, then restore")
    parser.add_argument("--keep", action="store_true",
                        help="with --scale, do NOT restore the previous replica count")
    parser.add_argument("--session-portability", action="store_true",
                        help="needs 2 web pods; combine with --scale web=2 --keep")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="TravelDemo2026!")
    args = parser.parse_args()

    prove_two_roles()
    prove_separate_pods()
    prove_isolation()
    prove_wiring()

    if args.restart_agents:
        restart_agents()
    if args.scale:
        role, _, count = args.scale.partition("=")
        prove_scale(role, int(count), restore=not args.keep and not args.session_portability)
    if args.session_portability:
        session_portability(2, args.email, args.password)
        if args.scale and not args.keep:
            role, _, _ = args.scale.partition("=")
            note(f"restoring {role} to 1")
            scale(role, 1)

    print(f"\n{_passes} passed, {len(_failures)} failed")
    for failure in _failures:
        print(f"  ! {failure}")
    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    main()
