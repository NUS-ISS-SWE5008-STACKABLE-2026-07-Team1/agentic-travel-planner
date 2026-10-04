"""deploy/set_image.sh — how the deploy pipeline points the app at its image.

The deploy of commit 7750e40 failed (2026-10-03): the short commit id was
written unquoted, YAML read it as 7750 x 10^40, and kustomize refused a number
where it wants a string. These run the pipeline's own script on ids that look
like numbers and have kustomize render the result, as the pipeline does.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "deploy" / "k8s" / "app"
SCRIPT = ROOT / "deploy" / "set_image.sh"
IMAGE = "us-west1-docker.pkg.dev/some-project/travel-planner/app"
BASH = shutil.which("bash")
KUBECTL = shutil.which("kubectl")

needs_bash = pytest.mark.skipif(not BASH, reason="bash not installed")
needs_kubectl = pytest.mark.skipif(not KUBECTL, reason="kubectl not installed")

# 7750e40: the id that failed. 1234567: all digits, an integer. 4bf5555: a
# normal id. 1e10: exponent form again. 0001234: leading zeros.
TAGS = ["7750e40", "1234567", "4bf5555", "1e10000", "0001234"]


def _app_copy(tmp_path: Path) -> Path:
    target = tmp_path / "app"
    shutil.copytree(APP, target)
    return target


def _set_image(app: Path, tag: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, SCRIPT.as_posix(), IMAGE, tag, (app / "kustomization.yaml").as_posix()],
        capture_output=True, text=True,
    )


@needs_bash
@pytest.mark.parametrize("tag", TAGS)
def test_the_tag_is_written_quoted(tmp_path, tag):
    app = _app_copy(tmp_path)
    done = _set_image(app, tag)
    assert done.returncode == 0, done.stdout + done.stderr
    text = (app / "kustomization.yaml").read_text(encoding="utf-8")
    assert f'    newTag: "{tag}"\n' in text
    assert f"    newName: {IMAGE}\n" in text


@needs_bash
@needs_kubectl
@pytest.mark.parametrize("tag", TAGS)
def test_kustomize_renders_every_tag_as_text(tmp_path, tag):
    app = _app_copy(tmp_path)
    assert _set_image(app, tag).returncode == 0
    rendered = subprocess.run([KUBECTL, "kustomize", str(app)],
                              capture_output=True, text=True)
    assert rendered.returncode == 0, rendered.stderr
    images = [line.split("image:", 1)[1].strip()
              for line in rendered.stdout.splitlines() if "image:" in line]
    assert images and all(image == f"{IMAGE}:{tag}" for image in images), images


@needs_kubectl
def test_the_old_unquoted_form_is_what_broke(tmp_path):
    """Pins the failure itself, so this file keeps meaning something."""
    app = _app_copy(tmp_path)
    path = app / "kustomization.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(
        "    newTag: 7750e40" if line.startswith("    newTag:") else line for line in lines
    ) + "\n", encoding="utf-8")
    rendered = subprocess.run([KUBECTL, "kustomize", str(app)],
                              capture_output=True, text=True)
    assert rendered.returncode != 0
    assert "newTag" in rendered.stderr


@needs_bash
def test_a_missed_substitution_fails_loudly(tmp_path):
    app = _app_copy(tmp_path)
    path = app / "kustomization.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("    newTag:", "    tag:"),
                    encoding="utf-8")
    done = _set_image(app, "4bf5555")
    assert done.returncode != 0
    assert "could not set the image tag" in done.stdout + done.stderr


def test_the_pipeline_uses_this_script():
    workflow = (ROOT / ".github" / "workflows" / "deploy-gke.yml").read_text(encoding="utf-8")
    assert 'bash deploy/set_image.sh "$IMAGE" "$TAG"' in workflow
    assert "newTag:\\).*|\\1 ${TAG}|" not in workflow, "the unquoted sed is back"
