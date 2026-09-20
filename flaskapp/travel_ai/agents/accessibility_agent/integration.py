"""Deterministic enforcement of accessibility decisions across peer findings."""

from __future__ import annotations

import re

from flaskapp.travel_ai.schemas import AgentFinding

VETO_PATTERN = re.compile(r"^VETO:\s*(.+?)\s+(?:—|-)\s+.+$", re.I)


def enforce_cross_agent_vetoes(
    findings: list[AgentFinding],
) -> tuple[list[AgentFinding], list[str]]:
    """Remove peer options named by accessibility vetoes before synthesis."""
    output = [finding.model_copy(deep=True) for finding in findings]
    access = next((item for item in output if item.agent == "accessibility_agent"), None)
    if access is None:
        return output, []
    veto_names = {
        match.group(1).strip().casefold(): match.group(1).strip()
        for warning in access.warnings
        if (match := VETO_PATTERN.match(warning.strip()))
    }
    removed: list[str] = []
    for finding in output:
        if finding.agent == "accessibility_agent":
            continue
        original_count = len(finding.options)
        kept = []
        for option in finding.options:
            if option.name.strip().casefold() in veto_names:
                removed.append(option.name)
            else:
                kept.append(option)
        finding.options = kept
        if len(kept) < original_count:
            finding.warnings.append("One or more options were removed by accessibility review.")
    return output, list(dict.fromkeys(removed))
