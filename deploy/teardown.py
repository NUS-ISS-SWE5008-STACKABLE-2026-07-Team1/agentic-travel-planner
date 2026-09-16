"""Remove everything the GKE test created, so nothing keeps billing.

DRY RUN BY DEFAULT: it lists what exists and what it would delete. Nothing is
touched without --yes.

    .venv\\Scripts\\python.exe deploy\\teardown.py          # show
    .venv\\Scripts\\python.exe deploy\\teardown.py --yes    # delete

Ordered so the expensive thing goes first: the cluster (pods and nodes) is the
only item that costs money by the hour; the rest costs cents or nothing but is
clutter, or is a standing permission that should not outlive the test.

Some things live outside Google Cloud and are only PRINTED, for you to do by
hand: the Supabase schema and database role, the OpenAI project key, and this
machine's MCP registrations. Render and its `Travelplanner_schema` are never
touched by any step here.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

PROJECT = "project-931fd286-f1d2-4105-9e5"
REGION = "us-west1"
CLUSTER = "travel-planner"
REPOSITORY = "travel-planner"
NODE_SA = "serviceAccount:1040773574528-compute@developer.gserviceaccount.com"
MCP_USER = "user:erinellateo@gmail.com"
BILLING_ACCOUNT = "01142B-B4310B-4AA253"
BUDGET_NAME = "travel-planner GKE test"


def run(command: str, check: bool = False) -> subprocess.CompletedProcess:
    # shell=True so Windows resolves gcloud.cmd.
    return subprocess.run(command, shell=True, capture_output=True, text=True, check=check)


def gcloud_json(command: str) -> list | dict:
    result = run(f"gcloud {command} --project={PROJECT} --format=json")
    return json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else []


def step(apply: bool, label: str, command: str, exists: bool) -> None:
    if not exists:
        print(f"  -        {label} (not found)")
        return
    if not apply:
        print(f"  WOULD    {label}\n             {command}")
        return
    print(f"  DELETING {label} ...", flush=True)
    result = run(command)
    print("           done" if result.returncode == 0 else f"           FAILED: {result.stderr.strip()[:300]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    apply = parser.parse_args().yes
    print("DELETING" if apply else "DRY RUN — nothing will be changed. Re-run with --yes to delete.\n")

    clusters = gcloud_json(f"container clusters list --filter=name={CLUSTER}")
    step(apply, f"GKE cluster {CLUSTER} (pods, nodes, NetworkPolicy, Secret)",
         f"gcloud container clusters delete {CLUSTER} --location={REGION} --project={PROJECT} --quiet",
         bool(clusters))

    repos = gcloud_json(f"artifacts repositories list --location={REGION} --filter=name~/{REPOSITORY}$")
    step(apply, f"Artifact Registry repository {REPOSITORY} (all images)",
         f"gcloud artifacts repositories delete {REPOSITORY} --location={REGION} --project={PROJECT} --quiet",
         bool(repos))

    # Monitoring and budgets go through the REST API, as deploy/monitoring.py
    # created them: the gcloud commands for both are alpha/beta components.
    for policy in _rest_list("alertPolicies"):
        step(apply, f"alert policy '{policy['displayName']}'", f"(REST) DELETE {policy['name']}", True)
        if apply:
            _rest_delete(policy["name"])

    for channel in _rest_list("notificationChannels"):
        step(apply, f"notification channel '{channel['displayName']}'",
             f"(REST) DELETE {channel['name']}", True)
        if apply:
            _rest_delete(channel["name"], force=True)

    budget = _rest_budget()
    step(apply, f"billing budget '{BUDGET_NAME}'", f"(REST) DELETE {budget}", bool(budget))
    if apply and budget:
        _rest_delete(budget, host="billingbudgets")

    for member, role, scope in (
        (NODE_SA, "roles/container.defaultNodeServiceAccount", "project"),
        (MCP_USER, "roles/mcp.toolUser", "project"),
    ):
        step(apply, f"IAM {role} for {member}",
             f'gcloud projects remove-iam-policy-binding {PROJECT} --member="{member}" '
             f'--role="{role}" --condition=None --quiet',
             _has_binding(member, role))
    # The registry-scoped reader grant disappears with the repository itself.

    print("""
Outside Google Cloud — do these by hand:

  Supabase (SQL editor). Removes the GKE test data and its login, nothing of Render's:
      drop schema travelplanner_gke cascade;
      drop role gke_app;

  OpenAI: delete the `travel-planner-gke` project, or revoke its API key.

  This machine: remove the MCP servers and the token helper.
      claude mcp remove gcp-gke --scope local
      claude mcp remove gcp-logging --scope local
      claude mcp remove gcp-monitoring --scope local
      del %USERPROFILE%\\.claude\\gcp-mcp-headers.js

  Still left, all free: the enabled APIs, and the empty project itself.
""")


def _token() -> str:
    return run("gcloud auth print-access-token", check=True).stdout.strip()


def _rest(method: str, url: str) -> dict:
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Bearer {_token()}", "x-goog-user-project": PROJECT})
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        print(f"           REST {method} failed: {error.code} {error.read().decode()[:200]}")
        return {}


def _rest_list(collection: str) -> list:
    """Alert policies or notification channels labelled app=travel-planner."""
    from urllib.parse import quote
    label_filter = quote('user_labels.app="travel-planner"')
    data = _rest("GET", f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/{collection}"
                        f"?filter={label_filter}")
    return data.get(collection, [])


def _rest_budget() -> str | None:
    data = _rest("GET", f"https://billingbudgets.googleapis.com/v1/billingAccounts/{BILLING_ACCOUNT}/budgets")
    return next((b["name"] for b in data.get("budgets", []) if b.get("displayName") == BUDGET_NAME), None)


def _rest_delete(name: str, host: str = "monitoring", force: bool = False) -> None:
    version = "v1" if host == "billingbudgets" else "v3"
    suffix = "?force=true" if force else ""
    _rest("DELETE", f"https://{host}.googleapis.com/{version}/{name}{suffix}")


def _has_binding(member: str, role: str) -> bool:
    result = run(f'gcloud projects get-iam-policy {PROJECT} --flatten="bindings[].members" '
                 f'--filter="bindings.members:{member} AND bindings.role:{role}" --format="value(bindings.role)"')
    return role in result.stdout


if __name__ == "__main__":
    sys.exit(main())
