"""Guards on deploy/k8s, the manifests applied by hand.

`kubectl apply -k deploy/k8s` is the manual path for infrastructure. Some
manifests carry `REPLACE_WITH_...` placeholders so that no project-specific
identity is committed; those are filled in elsewhere (the pipeline, or a
one-time bootstrap command). If a placeholder ever lands in the manual
bundle, applying it overwrites a real value in the cluster with a name that
means nothing. For the pipeline's RoleBinding that silently breaks every
later deploy. These tests make that a CI failure instead.

Read with plain string parsing, not PyYAML, which is not a direct dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

K8S = Path(__file__).resolve().parents[1] / "deploy" / "k8s"


def _bundle(directory: Path) -> list[str]:
    """The `resources:` list of a kustomization.yaml, in order."""
    lines = (directory / "kustomization.yaml").read_text(encoding="utf-8").splitlines()
    resources, inside = [], False
    for line in lines:
        if re.match(r"^resources:\s*$", line):
            inside = True
            continue
        if inside:
            if re.match(r"^\S", line):
                break
            item = re.match(r"^\s+-\s+(\S+)", line)
            if item:
                resources.append(item.group(1))
    return resources


def _placeholders(path: Path) -> list[str]:
    """Placeholder VALUES, e.g. REPLACE_WITH_GCP_DEPLOY_SERVICE_ACCOUNT.

    Comments are ignored, and so is the bare prefix: image-policy.yaml has to
    say `REPLACE_WITH_` in order to detect it, and that is not a placeholder.
    """
    body = "\n".join(
        line.split(" #")[0] for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    return re.findall(r"REPLACE_WITH_[A-Z0-9][A-Z0-9_]*", body)


def test_the_placeholder_detector_finds_real_placeholders():
    """A guard that cannot fail is worthless: prove it sees the RBAC one."""
    assert _placeholders(K8S / "deployer-rbac.yaml") == ["REPLACE_WITH_GCP_DEPLOY_SERVICE_ACCOUNT"]
    assert _placeholders(K8S / "app" / "kustomization.yaml") == ["REPLACE_WITH_ARTIFACT_REGISTRY_IMAGE"]


def test_the_manual_bundle_contains_no_placeholders():
    offenders = [name for name in _bundle(K8S) if _placeholders(K8S / name)]
    assert not offenders, (
        f"{offenders} carry a REPLACE_WITH_ placeholder but are part of "
        "`kubectl apply -k deploy/k8s`. Applying them would overwrite the real "
        "value in the cluster. Apply such files through their bootstrap command."
    )


def test_the_pipeline_permission_is_bootstrap_only():
    assert "deployer-rbac.yaml" not in _bundle(K8S)
    rbac = (K8S / "deployer-rbac.yaml").read_text(encoding="utf-8")
    assert "BOOTSTRAP ONLY" in rbac and "REPLACE_WITH_GCP_DEPLOY_SERVICE_ACCOUNT" in rbac


def test_the_image_policy_is_applied_with_the_infrastructure():
    assert "image-policy.yaml" in _bundle(K8S)


def _policy_pattern() -> str:
    text = (K8S / "image-policy.yaml").read_text(encoding="utf-8")
    # The CEL string literal is single-quoted with doubled backslashes.
    literal = re.search(r"c\.image\.matches\('([^']+)'\)", text).group(1)
    return literal.replace("\\\\", "\\")


def _allowed(image: str) -> bool:
    return "REPLACE_WITH_" not in image and re.match(_policy_pattern(), image) is not None


def test_the_image_policy_admits_real_images():
    for image in (
        "us-west1-docker.pkg.dev/some-project/travel-planner/app:79767db",
        "us-west1-docker.pkg.dev/some-project/travel-planner/app:v3",
        "europe-west2-docker.pkg.dev/another-project/travel-planner/app:1.2.3",
    ):
        assert _allowed(image), image


def test_the_image_policy_refuses_placeholders_and_foreign_images():
    for image in (
        "REPLACE_WITH_ARTIFACT_REGISTRY_IMAGE:79767db",
        "us-west1-docker.pkg.dev/REPLACE_WITH_PROJECT/travel-planner/app:79767db",
        "travel-planner:v3",
        "nginx:latest",
        "us-west1-docker.pkg.dev/some-project/travel-planner/app",
    ):
        assert not _allowed(image), image


def _max_replicas() -> dict[str, int]:
    """maxReplicas per autoscaler in hpa.yaml, keyed by its target Deployment."""
    text = (K8S / "hpa.yaml").read_text(encoding="utf-8")
    return {
        target: int(maximum)
        for target, maximum in re.findall(
            r"kind: Deployment, name: (\w+)\}.*?maxReplicas: (\d+)", text, re.S
        )
    }


def test_the_autoscalers_are_applied_with_the_infrastructure():
    assert "hpa.yaml" in _bundle(K8S)
    assert set(_max_replicas()) == {"web", "agents"}


def test_no_role_scales_past_one_pod_unless_any_pod_can_serve_any_request():
    """A second pod is only safe while no request depends on one pod's memory.

    web: the browser's status poll can reach any pod, so job state must be read
    from the database (`planning_jobs`), not a module dict. agents: tasks sit in
    an InMemoryTaskStore, so the client must make one blocking SendMessage and
    never poll GetTask, which could reach a sibling pod. If either regresses,
    this holds that role at one pod again.
    """
    source = Path(__file__).resolve().parents[1] / "flaskapp" / "travel_ai"
    jobs = (source / "jobs.py").read_text(encoding="utf-8")
    client = (source / "a2a_client.py").read_text(encoding="utf-8")
    any_pod_can_serve = {
        "web": "get_planning_job(" in jobs and "_jobs: dict[" not in jobs,
        "agents": "polling=False" in client and "get_task(" not in client,
    }
    for role, maximum in _max_replicas().items():
        if not any_pod_can_serve[role]:
            assert maximum == 1, (
                f"{role} answers some requests from one pod's memory, so a second "
                "pod breaks plans in flight. See this test's docstring."
            )
