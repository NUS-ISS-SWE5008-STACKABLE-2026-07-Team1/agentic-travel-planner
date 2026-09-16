"""Load the IAP OAuth client into the cluster as the `iap-oauth` Secret.

The client itself is created by hand in the Google Cloud console (Google Auth
Platform > Clients > Web application): for a project with no Workspace
organization, Google requires a custom OAuth client for IAP and offers no
gcloud command to create one. iap.yaml's BackendConfig reads this Secret.

Same discipline as create_secrets.py: the client secret is read with getpass,
never echoed, never written to a file, and sent over kubectl's stdin with
create/replace rather than apply.

    .venv\\Scripts\\python.exe deploy\\create_iap_secret.py
"""

from __future__ import annotations

import getpass
import json
import re
import sys

from create_secrets import CLUSTER_CONTEXT_SUFFIX, NAMESPACE, kubectl

SECRET_NAME = "iap-oauth"


def main() -> None:
    context = kubectl("config", "current-context").stdout.strip()
    if not context.endswith(CLUSTER_CONTEXT_SUFFIX):
        sys.exit(f"kubectl points at '{context}', not the travel-planner GKE cluster. Stopping.")

    client_id = input("OAuth client ID (visible, ends in .apps.googleusercontent.com): ").strip()
    if not re.fullmatch(r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", client_id):
        sys.exit("That does not look like an OAuth client ID. Nothing was created.")
    client_secret = getpass.getpass("OAuth client secret (typing is hidden): ").strip()
    if not client_secret:
        sys.exit("No client secret entered. Nothing was created.")

    manifest = json.dumps({
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": SECRET_NAME, "namespace": NAMESPACE},
        # The key names BackendConfig's oauthclientCredentials requires.
        "stringData": {"client_id": client_id, "client_secret": client_secret},
    })
    exists = kubectl("get", "secret", SECRET_NAME, "-n", NAMESPACE, check=False).returncode == 0
    result = kubectl("replace" if exists else "create", "-f", "-", stdin=manifest, check=False)
    if result.returncode != 0:
        sys.exit(f"kubectl failed: {result.stderr.strip()}")

    print(f"\nSecret '{SECRET_NAME}' {'replaced' if exists else 'created'} in '{NAMESPACE}'.")
    print("Now add this redirect URI to the same OAuth client in the console, if not done yet:")
    print(f"  https://iap.googleapis.com/v1/oauth/clientIds/{client_id}:handleRedirect")


if __name__ == "__main__":
    main()
