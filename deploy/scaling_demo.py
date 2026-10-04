"""Prove the autoscaling end to end, on the real cluster, at no model cost.

Brings up deploy/k8s-loadtest — production's own Deployments, Services,
autoscalers, NetworkPolicy and PodMonitoring in a throwaway namespace, with a
fake model that answers slowly — then sends plans in waves from INSIDE the
cluster and records, every 15 s, what the autoscalers saw and did:

    the gauges        plans in flight (web), A2A calls in flight (agents)
    the decision      desired vs. current replicas, from each HPA
    the outcome       every plan completed? any 404 on a status poll?
    the spread        which pod ran how many plans (planning_jobs.worker)
    the scale-down    pods removed after the load, without losing a plan

Why from inside the cluster: `kubectl port-forward` pins every connection to
ONE pod, so a load test driven through it would never reach the pods the
autoscaler adds. The load generator runs as a Job and goes through the web
Service, which spreads connections the way the load balancer does.

Why waves: a plan already queued stays on the pod that accepted it. Only
plans that arrive after a scale-out can land on the new pod.

    python deploy/scaling_demo.py                     # defaults, ~25 min
    python deploy/scaling_demo.py --plans 18 --waves 3 --wave-interval 150 \\
        --stub-delay 30 --cooldown-minutes 14 --report scaling-report.md
    python deploy/scaling_demo.py --keep              # leave the namespace up

Costs cents (a few small pods for under half an hour). Spends no OpenAI
tokens: no real key exists in that namespace. The namespace, its database
and the generated login are deleted at the end unless --keep.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

NAMESPACE = "travel-planner-loadtest"
PRODUCTION = "travel-planner"
PLACEHOLDER = "REPLACE_WITH_ARTIFACT_REGISTRY_IMAGE:v3"
LOGIN_EMAIL = "loadtest@example.com"  # matches deploy/k8s-loadtest/config.yaml
HERE = Path(__file__).resolve().parent


def kubectl(*args: str, stdin: str | None = None, check: bool = True,
            namespace: str | None = NAMESPACE) -> str:
    command = ["kubectl", *(["-n", namespace] if namespace else []), *args]
    done = subprocess.run(command, input=stdin, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(command[:6])}...: {done.stderr.strip()}")
    return done.stdout


def production_image() -> str:
    return kubectl("get", "deploy", "web", "-o",
                   "jsonpath={.spec.template.spec.containers[0].image}", namespace=PRODUCTION)


def bring_up(image: str, stub_delay: float, password: str) -> None:
    from werkzeug.security import generate_password_hash

    bundle = subprocess.run(
        ["kubectl", "kustomize", "--load-restrictor", "LoadRestrictionsNone",
         str(HERE / "k8s-loadtest")],
        capture_output=True, text=True, check=True,
    ).stdout
    if f"namespace: {PRODUCTION}\n" in bundle:
        raise RuntimeError("the load-test bundle would touch production; refusing")
    bundle = bundle.replace(PLACEHOLDER, image)
    bundle = bundle.replace('value: "30"', f'value: "{stub_delay:g}"', 1)

    # Namespace first, then the Secrets the pods need, then everything else.
    namespace_doc = next(d for d in bundle.split("\n---\n") if "kind: Namespace" in d)
    kubectl("apply", "-f", "-", stdin=namespace_doc, namespace=None)
    db_password = secrets.token_urlsafe(24)
    # stringData via stdin: generated values never reach a command line or a file.
    kubectl("apply", "-f", "-", stdin=json.dumps({"apiVersion": "v1", "kind": "List", "items": [
        # Read by postgres and by the load generator only — not envFrom'd
        # into the app pods, unlike app-secrets.
        {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "loadtest-db"},
         "stringData": {"password": db_password, "login_password": password}},
        {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "app-secrets"},
         "stringData": {
             "SECRET_KEY": secrets.token_urlsafe(32),
             "DATABASE_URL": f"postgresql://travel:{db_password}@postgres:5432/travel",
             "LOGIN_PASSWORD_HASH": generate_password_hash(password),
         }},
    ]}))
    kubectl("apply", "-f", "-", stdin=bundle)
    for name in ("postgres", "stub", "web", "agents"):
        kubectl("rollout", "status", f"deploy/{name}", "--timeout=12m")


def start_load(plans: int, waves: int, interval: float) -> None:
    script = kubectl("create", "configmap", "load-test-script",
                     f"--from-file=load_test.py={HERE / 'load_test.py'}",
                     "--dry-run=client", "-o", "yaml")
    kubectl("apply", "-f", "-", stdin=script)
    image = kubectl("get", "deploy", "web", "-o",
                    "jsonpath={.spec.template.spec.containers[0].image}")
    job = {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "load"},
        "spec": {"backoffLimit": 0, "template": {"spec": {
            "restartPolicy": "Never",
            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
            "containers": [{
                "name": "load", "image": image,
                "command": ["python", "/load/load_test.py", "--base-url", "http://web",
                            "--plans", str(plans), "--waves", str(waves),
                            "--wave-interval", str(interval), "--timeout", "1500"],
                "env": [
                    {"name": "E2E_LOGIN_EMAIL", "value": LOGIN_EMAIL},
                    {"name": "E2E_LOGIN_PASSWORD", "valueFrom": {"secretKeyRef": {
                        "name": "loadtest-db", "key": "login_password"}}},
                ],
                "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}},
                "volumeMounts": [{"name": "script", "mountPath": "/load"}],
            }],
            "volumes": [{"name": "script", "configMap": {"name": "load-test-script"}}],
        }}},
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(job))


def db_stats() -> tuple[int, int]:
    """(sessions ever opened on the database, connections open now).

    `sessions` counts every connection made, however short, which a 15 s
    sample of open connections would miss: unpooled, each call's connection
    lives for milliseconds. Each call here opens one session itself; the
    caller subtracts those. Excludes this probe from "open now".
    """
    pod = kubectl("get", "pods", "-l", "app=postgres", "-o", "jsonpath={.items[0].metadata.name}")
    out = kubectl("exec", pod, "--", "psql", "-U", "travel", "-d", "travel", "-tAc",
                  "SELECT (SELECT sessions FROM pg_stat_database WHERE datname = 'travel'), "
                  "(SELECT count(*) FROM pg_stat_activity WHERE datname = 'travel' "
                  "AND pid <> pg_backend_pid())", check=False).strip()
    try:
        opened, open_now = (int(x) for x in out.split("|"))
    except ValueError:
        return -1, -1
    return opened, open_now


def snapshot(started: float) -> dict:
    hpas = json.loads(kubectl("get", "hpa", "-o", "json"))["items"]
    pods = json.loads(kubectl("get", "pods", "-o", "json"))["items"]
    row = {"t": round(time.monotonic() - started)}
    for hpa in hpas:
        role = hpa["metadata"]["name"]
        status = hpa.get("status", {})
        gauge = next((m["pods"]["current"]["averageValue"] for m in status.get("currentMetrics") or []
                      if m.get("type") == "Pods" and m.get("pods", {}).get("current")), "?")
        ready = sum(1 for p in pods if p["metadata"].get("labels", {}).get("app") == role
                    and p["status"].get("phase") == "Running"
                    and all(c.get("ready") for c in p["status"].get("containerStatuses", [])))
        row[role] = {"gauge": gauge, "desired": status.get("desiredReplicas"),
                     "current": status.get("currentReplicas"), "ready": ready}
    job = kubectl("get", "job", "load", "-o", "jsonpath={.status.succeeded}{.status.failed}",
                  check=False)
    row["job_done"] = bool(job.strip())
    row["db_opened"], row["db_open"] = db_stats()
    return row


def _per_pod(quantity: str) -> str:
    """The HPA reports an average as a Kubernetes quantity: "3666m" is 3.67."""
    if quantity.endswith("m"):
        return f"{int(quantity[:-1]) / 1000:.2f}".rstrip("0").rstrip(".")
    return quantity


def _line(row: dict) -> str:
    cells = [f"{row['t']:>5}s"]
    for role in ("web", "agents"):
        r = row.get(role, {})
        cells.append(f"{role}: {_per_pod(str(r.get('gauge', '?'))):>5} per pod, "
                     f"desired {r.get('desired', '?')}, pods {r.get('ready', '?')}")
    cells.append(f"db: {row.get('db_open', '?')} open")
    return "  |  ".join(cells)


def per_pod_spread() -> str:
    pod = kubectl("get", "pods", "-l", "app=postgres", "-o", "jsonpath={.items[0].metadata.name}")
    return kubectl("exec", pod, "--", "psql", "-U", "travel", "-d", "travel", "-c",
                   "SELECT worker AS web_pod, status, count(*) AS plans "
                   "FROM planning_jobs GROUP BY worker, status ORDER BY worker, status")


def rescale_events() -> str:
    events = json.loads(kubectl("get", "events", "--field-selector",
                                "reason=SuccessfulRescale", "-o", "json"))["items"]
    events.sort(key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "")
    return "\n".join(f"{e.get('lastTimestamp') or e.get('eventTime')}  "
                     f"{e['involvedObject']['name']:<7} {e['message']}" for e in events)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plans", type=int, default=18)
    parser.add_argument("--waves", type=int, default=3)
    parser.add_argument("--wave-interval", type=float, default=150)
    parser.add_argument("--stub-delay", type=float, default=30,
                        help="seconds the fake model waits per reply")
    parser.add_argument("--cooldown-minutes", type=float, default=14,
                        help="keep watching after the load ends, to record scale-down")
    parser.add_argument("--image", help="default: the image production web runs")
    parser.add_argument("--report", type=Path, help="also write the report here")
    parser.add_argument("--keep", action="store_true", help="do not delete the namespace")
    args = parser.parse_args()

    image = args.image or production_image()
    password = secrets.token_urlsafe(18)
    report: list[str] = [
        f"# Autoscaling proof — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
        f"Image `{image.rsplit('/', 1)[-1]}` · {args.plans} plans in {args.waves} waves "
        f"{args.wave_interval:g}s apart · fake model {args.stub_delay:g}s per reply · "
        f"namespace `{NAMESPACE}`", "",
    ]
    try:
        print(f"bringing up {NAMESPACE} on {image} ...", flush=True)
        bring_up(image, args.stub_delay, password)
        print("up. starting the load.\n", flush=True)
        start_load(args.plans, args.waves, args.wave_interval)

        started = time.monotonic()
        timeline, load_ended = [], None
        while True:
            row = snapshot(started)
            timeline.append(row)
            print(_line(row), flush=True)
            if row["job_done"] and load_ended is None:
                load_ended = time.monotonic()
                print(f"\nload finished; watching {args.cooldown_minutes:g} more minutes "
                      "for scale-down\n", flush=True)
            if load_ended and time.monotonic() - load_ended > args.cooldown_minutes * 60:
                break
            if time.monotonic() - started > 75 * 60:
                print("giving up after 75 minutes", flush=True)
                break
            time.sleep(15)

        load_log = kubectl("logs", "job/load", check=False)
        spread = per_pod_spread()
        events = rescale_events()
        report += [
            "## What the autoscalers saw and did (every 15 s)", "", "```",
            *(_line(r) for r in timeline), "```", "",
            "## Scaling decisions (Kubernetes events)", "", "```", events or "(none)", "```", "",
            "## Which web pod ran which plans", "", "```", spread.strip(), "```", "",
            "## The load generator's verdict", "", "```", load_log.strip(), "```",
        ]
        verdict = "PASS" if load_log.rstrip().endswith("PASS") else "FAIL"
        peak = {role: max((r.get(role, {}).get("ready") or 0) for r in timeline)
                for role in ("web", "agents")}
        final = {role: timeline[-1].get(role, {}).get("ready") for role in ("web", "agents")}
        # Sessions opened by the app during the load: the counter's rise minus
        # the one session each sample's own probe opened.
        loaded = [r for r in timeline if r.get("db_opened", -1) >= 0]
        app_sessions = (loaded[-1]["db_opened"] - loaded[0]["db_opened"] - (len(loaded) - 1)
                        if len(loaded) > 1 else "?")
        peak_open = max((r.get("db_open", 0) for r in loaded), default="?")
        report[3:3] = [f"**{verdict}** · peak pods: web {peak['web']}, agents {peak['agents']} · "
                       f"after cooldown: web {final['web']}, agents {final['agents']}", "",
                       f"Database: **{app_sessions} connections opened** by the app over the run, "
                       f"at most {peak_open} open at once (sampled every 15 s).", ""]
    finally:
        if args.keep:
            print(f"\n--keep: namespace {NAMESPACE} left running; delete it with\n"
                  f"  kubectl delete namespace {NAMESPACE}", flush=True)
        else:
            print(f"\ndeleting namespace {NAMESPACE} ...", flush=True)
            kubectl("delete", "namespace", NAMESPACE, "--wait=false", namespace=None, check=False)

    text = "\n".join(report) + "\n"
    print("\n" + text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
        print(f"report written to {args.report}")
    sys.exit(0 if "**PASS**" in text else 1)


if __name__ == "__main__":
    main()
