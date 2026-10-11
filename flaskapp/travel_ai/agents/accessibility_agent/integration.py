"""Deterministic enforcement of accessibility decisions across peer findings."""

from __future__ import annotations

from flaskapp.travel_ai.schemas import AgentFinding


def parse_veto(warning: str) -> str | None:
    r"""The option a `VETO: <option> — <reason>` warning names, or None.

    Accepts what `^VETO:\s*(.+?)\s+(?:—|-)\s+.+$` (case-insensitive) did: the
    first spaced dash or em dash with text on both sides, where neither the
    option nor the reason spans a line break. Parsed by hand in one linear
    pass because that pattern backtracks polynomially on a long line of spaces
    and dashes, and these warnings are model-written text.
    """
    text = warning.strip()
    if text[:5].casefold() != "veto:":
        return None
    body = text[5:]
    # next_text[j]: the first non-space index at or after j.
    next_text = [len(body)] * (len(body) + 1)
    for j in range(len(body) - 1, -1, -1):
        next_text[j] = j if not body[j].isspace() else next_text[j + 1]
    last_newline = body.rfind("\n")
    seen_text = option_broken = pending_newline = False
    for i, ch in enumerate(body):
        reason_start = next_text[i + 1] if i + 1 < len(body) else len(body)
        if (seen_text and not option_broken and ch in "—-" and i > 0
                and body[i - 1].isspace() and body[i + 1:i + 2].isspace()
                and reason_start < len(body) and last_newline < reason_start):
            return body[:i].strip()
        if ch.isspace():
            pending_newline = pending_newline or (seen_text and ch == "\n")
        else:
            # A line break followed by more option text splits the option.
            option_broken = option_broken or pending_newline
            seen_text = True
    return None


def enforce_cross_agent_vetoes(
    findings: list[AgentFinding],
) -> tuple[list[AgentFinding], list[str]]:
    """Remove peer options named by accessibility vetoes before synthesis."""
    output = [finding.model_copy(deep=True) for finding in findings]
    access = next((item for item in output if item.agent == "accessibility_agent"), None)
    if access is None:
        return output, []
    veto_names = {
        option.casefold(): option
        for warning in access.warnings
        if (option := parse_veto(warning))
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
