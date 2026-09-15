"""Fail if the installed packages no longer satisfy requirements.txt.

    python -m pip install -r requirements.lock
    python scripts/check_requirements_lock.py

Render and CI install `requirements.lock`, but people edit `requirements.txt`.
Nothing links the two, so a range raised in requirements.txt without
regenerating the lock would be silently ignored: CI would keep testing, and
pip-audit would keep scanning, the old versions. Run after installing the lock,
this turns that drift into a failed build with the command that fixes it.

Only the direct requirements are checked. Everything they pull in is already
pinned and hash-checked by the lock itself.
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.requirements import Requirement

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"
REGENERATE = (
    "uv pip compile requirements.txt --universal --python-version 3.11 "
    "--generate-hashes --output-file requirements.lock"
)


def main() -> int:
    problems = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            problems.append(f"{requirement.name}: in requirements.txt but not in the lock")
            continue
        if not requirement.specifier.contains(installed, prereleases=True):
            problems.append(
                f"{requirement.name}: lock has {installed}, "
                f"requirements.txt asks for {requirement.specifier}"
            )

    if problems:
        print("requirements.lock is out of date with requirements.txt:")
        for problem in problems:
            print(f"  - {problem}")
        print(f"\nRegenerate it:\n  {REGENERATE}")
        return 1
    print("requirements.lock satisfies requirements.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
