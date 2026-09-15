"""Create the GKE deployment's database role and its Kubernetes Secret.

GKE does not reuse Render's database credentials. It gets its own Postgres
role, `gke_app`, whose only privileges are USAGE and CREATE on the
`travelplanner_gke` schema — so the cluster cannot read or write Render's
`Travelplanner_schema`, and nothing about Render changes.

Designed so that no secret is ever written down in plaintext:

- The database password is generated here and never printed. What the SQL
  editor receives is its SCRAM-SHA-256 *verifier* — the same one-way hash
  Postgres would store — so Supabase's query history holds nothing that can log
  in.
- Values you supply are read with getpass (not echoed, not in shell history).
- The Secret goes to the cluster over kubectl's stdin, never to a file, and
  with `create`/`replace` rather than `apply`, which would copy every value into
  a last-applied-configuration annotation.

Re-running rotates everything: a new SECRET_KEY, a new database password (the
SQL it prints is an ALTER when the role already exists), and a replaced Secret.
Restart the pods afterwards so they read the new values.

    .venv\\Scripts\\python.exe deploy\\create_secrets.py
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import secrets
import subprocess
import sys

CLUSTER_CONTEXT_SUFFIX = "_us-west1_travel-planner"
NAMESPACE = "travel-planner"
SECRET_NAME = "app-secrets"
ROLE = "gke_app"
SCHEMA = "travelplanner_gke"
PROJECT_REF = "kgwanhhqlvozuxnwyhml"
POOLER_HOST = "aws-0-us-west-2.pooler.supabase.com"


def scram_sha256_verifier(password: str, iterations: int = 4096) -> str:
    """The string Postgres stores for a SCRAM-SHA-256 password (RFC 5802/7677).

    Postgres accepts this in place of a plaintext password in CREATE/ALTER ROLE
    and stores it as-is. The generated password is URL-safe ASCII, so SASLprep
    normalisation is the identity and can be skipped.
    """
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda raw: base64.b64encode(raw).decode()
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def kubectl(*args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], input=stdin, text=True,
                          capture_output=True, check=check)


def main() -> None:
    context = kubectl("config", "current-context").stdout.strip()
    if not context.endswith(CLUSTER_CONTEXT_SUFFIX):
        sys.exit(f"kubectl points at '{context}', not the travel-planner GKE cluster. Stopping.")

    db_password = secrets.token_urlsafe(32)
    verifier = scram_sha256_verifier(db_password)

    print("\n=== 1. Run this in the Supabase SQL editor ===\n")
    print(f"""do $$
begin
  if exists (select from pg_roles where rolname = '{ROLE}') then
    alter role {ROLE} with login password '{verifier}';
  else
    create role {ROLE} with login password '{verifier}';
  end if;
end $$;
grant usage, create on schema {SCHEMA} to {ROLE};""")
    print("\nIt should report: Success. No rows returned")
    input("\nPress Enter once it has run successfully... ")

    print("\n=== 2. Values for the cluster (typing is hidden) ===\n")
    openai_key = getpass.getpass("OPENAI_API_KEY (the travel-planner-gke project key): ").strip()
    if not openai_key.startswith("sk-"):
        sys.exit("That does not look like an OpenAI key (expected it to start with sk-). Nothing was created.")
    serper_key = getpass.getpass("SERPER_API_KEY (optional, Enter to skip): ").strip()
    admin_emails = input("ADMIN_EMAILS (comma-separated, visible): ").strip()

    data = {
        "OPENAI_API_KEY": openai_key,
        # No password in the DSN: libpq reads PGPASSWORD, as on Render.
        "DATABASE_URL": f"postgresql://{ROLE}.{PROJECT_REF}@{POOLER_HOST}:5432/postgres",
        "PGPASSWORD": db_password,
        "SECRET_KEY": secrets.token_urlsafe(48),
        "ADMIN_EMAILS": admin_emails,
    }
    if serper_key:
        data["SERPER_API_KEY"] = serper_key

    manifest = json.dumps({
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": SECRET_NAME, "namespace": NAMESPACE},
        "stringData": data,
    })

    # The namespace is normally created by `kubectl apply -k deploy/k8s`, which
    # has not run yet at this point in the setup.
    if kubectl("get", "namespace", NAMESPACE, check=False).returncode != 0:
        kubectl("create", "namespace", NAMESPACE)
    exists = kubectl("get", "secret", SECRET_NAME, "-n", NAMESPACE, check=False).returncode == 0
    result = kubectl("replace" if exists else "create", "-f", "-", stdin=manifest, check=False)
    if result.returncode != 0:
        # kubectl's error names the resource, never the values.
        sys.exit(f"kubectl failed: {result.stderr.strip()}")

    print(f"\n=== Done: Secret '{SECRET_NAME}' {'replaced' if exists else 'created'} "
          f"in namespace '{NAMESPACE}' ===")
    print("Keys stored:", ", ".join(sorted(data)))
    print("DATABASE_URL:", data["DATABASE_URL"], " (contains no password)")
    print("\nThe database password and SECRET_KEY exist only in the cluster now.")
    print("To change them later, re-run this script. Nothing needs saving.")


if __name__ == "__main__":
    main()
