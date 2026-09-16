"""Create the GKE deployment's alerts and spend budget. Safe to re-run.

One email notification channel, four alert policies and one billing budget,
each found by display name first so a second run changes nothing. Everything
is labelled or named `travel-planner` so deploy/teardown.py can find it.

    .venv\\Scripts\\python.exe deploy\\monitoring.py --email you@example.com

The filters were each run against three days of real logs before being
committed here (2026-09-17), with these findings baked in:

- Cloud Logging tags every container **stderr** line severity ERROR, and
  gunicorn and uvicorn write their INFO lines to stderr. A `severity>=ERROR`
  alert would fire on every pod start, so the app-error alert matches text.
- Startup-probe failures are routine on every rollout (the app takes ~20s to
  import), so only liveness and readiness failures alert.
- `FailedScheduling` is routine while Autopilot adds a node; `FailedScaleUp`
  is the one that means it could not (it fired on the trial's SSD quota).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "project-931fd286-f1d2-4105-9e5"
PROJECT_NUMBER = "1040773574528"
BILLING_ACCOUNT = "01142B-B4310B-4AA253"
CLUSTER = "travel-planner"
NAMESPACE = "travel-planner"
LABELS = {"app": "travel-planner"}
BUDGET_NAME = "travel-planner GKE test"

_EVENTS = (
    f'logName="projects/{PROJECT}/logs/events" '
    f'AND resource.labels.cluster_name="{CLUSTER}" '
    f'AND jsonPayload.involvedObject.namespace="{NAMESPACE}"'
)
_CONTAINERS = (
    f'resource.type="k8s_container" AND resource.labels.cluster_name="{CLUSTER}" '
    f'AND resource.labels.namespace_name="{NAMESPACE}"'
)


def _log_policy(name: str, log_filter: str, what: str, check: str) -> dict:
    return {
        "displayName": name,
        "userLabels": LABELS,
        "combiner": "OR",
        "conditions": [{"displayName": name, "conditionMatchedLog": {"filter": log_filter}}],
        # One email per half hour per incident, not one per log line.
        "alertStrategy": {"notificationRateLimit": {"period": "1800s"}, "autoClose": "3600s"},
        "documentation": {"mimeType": "text/markdown", "content": f"{what}\n\n**Check:** {check}"},
    }


POLICIES = [
    {
        "displayName": "travel-planner: container restarted",
        "userLabels": LABELS,
        "combiner": "OR",
        "conditions": [{
            "displayName": "restart_count increased",
            "conditionThreshold": {
                "filter": (
                    'metric.type="kubernetes.io/container/restart_count" '
                    f'AND resource.type="k8s_container" AND resource.labels.cluster_name="{CLUSTER}" '
                    f'AND resource.labels.namespace_name="{NAMESPACE}"'
                ),
                "aggregations": [{
                    "alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_DELTA",
                    "crossSeriesReducer": "REDUCE_SUM", "groupByFields": ["resource.label.pod_name"],
                }],
                "comparison": "COMPARISON_GT", "thresholdValue": 0, "duration": "0s",
                "trigger": {"count": 1},
            },
        }],
        "alertStrategy": {"autoClose": "3600s"},
        "documentation": {"mimeType": "text/markdown", "content": (
            "A web or agents container restarted: a crash, an OOM kill, or a failed liveness probe. "
            "Any plan running at the time was lost, along with the in-memory job state.\n\n"
            "**Check:** `kubectl -n travel-planner get pods` (LAST STATE) and "
            "`kubectl -n travel-planner logs <pod> --previous`."
        )},
    },
    _log_policy(
        "travel-planner: liveness or readiness probe failing",
        f'{_EVENTS} AND jsonPayload.reason="Unhealthy" AND '
        '(jsonPayload.message:"Liveness probe failed" OR jsonPayload.message:"Readiness probe failed")',
        "A running pod failed a health check. Readiness failing on web usually means the database "
        "is unreachable; liveness failing repeatedly leads to a restart.",
        "`kubectl -n travel-planner describe pod <pod>` (Events) and the /readyz response.",
    ),
    _log_policy(
        "travel-planner: pod cannot run",
        f'{_EVENTS} AND jsonPayload.reason=("FailedScaleUp" OR "BackOff" OR "Failed")',
        "A pod cannot start or stay up: Autopilot could not add a node (e.g. GCE quota), "
        "an image could not be pulled, or a container is crash-looping.",
        "`kubectl -n travel-planner get pods`, `kubectl -n travel-planner get events --sort-by=.lastTimestamp`.",
    ),
    _log_policy(
        "travel-planner: application error logged",
        f'{_CONTAINERS} AND textPayload=~"Traceback|\\[ERROR\\]|^ERROR:|CRITICAL"',
        "The application logged a traceback or an explicit error line.",
        "`kubectl -n travel-planner logs deploy/web` or `deploy/agents` around the alert time.",
    ),
]


class Api:
    def __init__(self) -> None:
        # gcloud is a .cmd on Windows; the shell resolves it.
        self.token = subprocess.run(
            "gcloud auth print-access-token", shell=True, capture_output=True, text=True, check=True
        ).stdout.strip()

    def call(self, method: str, url: str, body: dict | None = None, query: dict | None = None) -> dict:
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                     "x-goog-user-project": PROJECT},
        )
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            sys.exit(f"{method} {url.split('?')[0]} -> {error.code}: {error.read().decode()[:500]}")


def ensure_channel(api: Api, email: str) -> str:
    base = f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/notificationChannels"
    found = api.call("GET", base, query={
        "filter": f'type="email" AND labels.email_address="{email}"'}).get("notificationChannels", [])
    if found:
        print(f"exists   channel  {email}")
        return found[0]["name"]
    created = api.call("POST", base, {
        "type": "email", "displayName": f"travel-planner alerts ({email})",
        "labels": {"email_address": email}, "userLabels": LABELS,
    })
    print(f"created  channel  {email}")
    return created["name"]


def ensure_policies(api: Api, channel: str) -> None:
    base = f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/alertPolicies"
    for policy in POLICIES:
        name = policy["displayName"]
        found = api.call("GET", base, query={"filter": f'display_name="{name}"'}).get("alertPolicies", [])
        if found:
            print(f"exists   policy   {name}")
            continue
        api.call("POST", base, {**policy, "notificationChannels": [channel]})
        print(f"created  policy   {name}")


def ensure_budget(api: Api, channel: str, amount: int) -> None:
    base = f"https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING_ACCOUNT}/budgets"
    for budget in api.call("GET", base).get("budgets", []):
        if budget.get("displayName") == BUDGET_NAME:
            print(f"exists   budget   {BUDGET_NAME}")
            return
    api.call("POST", base, {
        "displayName": BUDGET_NAME,
        "budgetFilter": {
            "projects": [f"projects/{PROJECT_NUMBER}"],
            # Gross spend. Including credits would net every cost to zero while
            # the Free Trial pays, and the budget would never alert.
            "creditTypesTreatment": "EXCLUDE_ALL_CREDITS",
        },
        # No currency code: it must match the billing account's, which this
        # script does not know, and omitting it uses the account's.
        "amount": {"specifiedAmount": {"units": str(amount)}},
        "thresholdRules": [{"thresholdPercent": p} for p in (0.25, 0.5, 0.9, 1.0)],
        "notificationsRule": {"monitoringNotificationChannels": [channel],
                              "disableDefaultIamRecipients": False},
    })
    print(f"created  budget   {BUDGET_NAME} ({amount}, alerts at 25/50/90/100%)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create travel-planner GKE alerts and budget.")
    parser.add_argument("--email", required=True, help="where alerts are sent")
    parser.add_argument("--budget", type=int, default=100, help="in the billing account's currency")
    args = parser.parse_args()
    api = Api()
    channel = ensure_channel(api, args.email)
    ensure_policies(api, channel)
    ensure_budget(api, channel, args.budget)


if __name__ == "__main__":
    main()
