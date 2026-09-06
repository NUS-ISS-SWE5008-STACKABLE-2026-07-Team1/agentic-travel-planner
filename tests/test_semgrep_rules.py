"""The semgrep rules must actually fire.

`.semgrep/llm-agent.yml` is a blocking CI gate whose header promises that every
rule reports zero findings, "so the rule fires when someone breaks it, not when
someone writes it". Only half of that promise is self-checking: CI proves the
rules are quiet, and nothing proved they could ever speak.

That is not hypothetical. The two tool-loop rules were added and reported zero
findings on the whole codebase — which looked like success, and was actually a
rule that could not match anything. The next draft matched the violations but
also fired on a correct function whose only difference was a missing type
annotation. Both mistakes are invisible without fixtures, so here they are.

`tests/fixtures/semgrep/` holds one file of deliberate violations and one of the
bounded shapes that must stay silent. Neither is imported or executed; they exist
to be scanned.

Skipped when semgrep is not installed, so a contributor without it can still run
the suite. CI installs it (`.github/workflows/ci-checks.yml`), so the gate is
real there.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".semgrep"
FIXTURES = Path(__file__).parent / "fixtures" / "semgrep"

pytestmark = pytest.mark.skipif(
    shutil.which("semgrep") is None, reason="semgrep is not installed"
)


def _scan(path: Path) -> list[dict]:
    result = subprocess.run(
        ["semgrep", "scan", "--config", str(CONFIG), "--metrics=off",
         "--no-git-ignore", "--json", str(path)],
        capture_output=True, text=True, cwd=ROOT, timeout=300,
    )
    assert result.stdout, f"semgrep produced no output:\n{result.stderr}"
    return json.loads(result.stdout)["results"]


def _rule_ids(results: list[dict]) -> set[str]:
    return {result["check_id"].split(".")[-1] for result in results}


def test_violations_are_reported():
    """Each deliberate violation must be caught by the rule aimed at it."""
    found = _rule_ids(_scan(FIXTURES / "loop_violations.py"))

    assert "llm-tool-loop-without-budget" in found, (
        "an unbounded tool loop was not reported"
    )
    assert "llm-tool-result-into-inventory-row" in found, (
        "an inventory row built from model output was not reported"
    )


def test_every_violation_line_is_reported():
    """Three violations are written; three must be found. A rule that catches the
    first shape and misses a variant is a rule that will miss the real one."""
    results = _scan(FIXTURES / "loop_violations.py")
    assert len(results) == 3, (
        "expected one finding per violation, got "
        f"{[(r['check_id'].split('.')[-1], r['start']['line']) for r in results]}"
    )


def test_compliant_shapes_are_silent():
    """The half that stops the gate becoming noise. A budget passed as a
    parameter, a budget reached through a ToolContext, and a candidate built from
    a provider row are all correct and must not be reported."""
    results = _scan(FIXTURES / "loop_compliant.py")

    assert results == [], (
        "a correct shape was reported: "
        f"{[(r['check_id'].split('.')[-1], r['start']['line']) for r in results]}"
    )


def test_the_real_agent_package_is_clean():
    """The promise in the file header, checked against the code it describes."""
    results = _scan(ROOT / "flaskapp")
    assert results == [], (
        f"{[(r['check_id'].split('.')[-1], r['path'], r['start']['line']) for r in results]}"
    )
