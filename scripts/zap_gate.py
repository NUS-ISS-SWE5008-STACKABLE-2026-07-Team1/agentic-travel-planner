"""Fail the DAST job when ZAP found something at or above a risk threshold.

    python scripts/zap_gate.py zap-authenticated-report.json --min-risk medium

Until 2026-10-19 the scan's findings were advisory: `.zap/authenticated-scan.yaml`
has no risk threshold and no `alertFilter`, so `ci-dast.yml` failed only when the
*scan itself* broke — a lost session, an unrendered placeholder. A new High-risk
alert would have been reported in an artifact nobody opened. "We run DAST" was
true; "DAST gates our merges on findings" was not, and that gap is exactly the
kind a reader of the pipeline would assume away.

**Why this, and not ZAP's own gating.** The Automation Framework has an
`exitStatus` job that can set a process exit code by risk level. Three reasons it
is not used here. It is coupled to whatever add-on version the pinned action
image ships, and an unknown job type fails with "unknown job type" rather than
"you have a Medium finding". It emits a bare exit code, so the failure cannot
name the alert — and a gate nobody can act on gets deleted. And it cannot be
exercised without a twenty-minute scan, where this has unit tests.

**Fail closed.** A missing, unreadable or shapeless report exits non-zero. A gate
that passes when it cannot see is the same failure as the clean report from a
logged-out scan that `ci-dast.yml` was written to prevent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ZAP's traditional-json template writes riskcode as a STRING, and uses "-1" for
# an alert an alertFilter marked a false positive. That is not a risk level and
# must not be compared as one.
RISK_NAMES = {0: "Informational", 1: "Low", 2: "Medium", 3: "High"}
FALSE_POSITIVE = -1

THRESHOLDS = {"informational": 0, "low": 1, "medium": 2, "high": 3}


class ReportUnreadable(Exception):
    """The report could not be read or did not have the shape ZAP produces."""


def load_alerts(path: Path) -> list[dict]:
    """Every alert in the report, flattened across sites.

    A plan can scan more than one context, so alerts are nested one level deeper
    than people expect. Counting only `site[0]` would quietly ignore findings.
    """
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReportUnreadable(f"no report at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReportUnreadable(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(report, dict) or "site" not in report:
        raise ReportUnreadable(
            f"{path} has no 'site' key — this is not a ZAP traditional-json report"
        )

    alerts = []
    for site in report.get("site") or []:
        alerts.extend(site.get("alerts") or [])
    return alerts


def risk_of(alert: dict) -> int:
    """The alert's risk as an int, or FALSE_POSITIVE when it is not a risk."""
    try:
        return int(alert.get("riskcode", FALSE_POSITIVE))
    except (TypeError, ValueError):
        # Unparseable is not "safe". Treat it as the highest risk so a format
        # change surfaces as a failed gate rather than a silent pass.
        return max(RISK_NAMES)


def census(alerts: list[dict]) -> str:
    """One line naming how many alerts sit at each risk. Printed pass or fail.

    This is the evidence a run produces even when nothing is wrong, which is
    what makes "DAST is green" mean something specific.
    """
    counts = {}
    for alert in alerts:
        risk = risk_of(alert)
        if risk == FALSE_POSITIVE:
            continue
        counts[risk] = counts.get(risk, 0) + 1
    if not counts:
        return "no alerts"
    return ", ".join(
        f"{counts[risk]} {RISK_NAMES.get(risk, f'risk {risk}')}"
        for risk in sorted(counts, reverse=True)
    )


def describe(alert: dict) -> str:
    """One alert, with enough detail to act on without opening the artifact."""
    risk = RISK_NAMES.get(risk_of(alert), "?")
    instances = alert.get("instances") or []
    example = instances[0].get("uri", "") if instances else ""
    line = (
        f"  [{risk}] {alert.get('pluginid', '?'):<8} {alert.get('alert', '?')}"
        f"  ({len(instances)} instance(s))"
    )
    return f"{line}\n           e.g. {example}" if example else line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--min-risk", default="medium", choices=sorted(THRESHOLDS))
    parser.add_argument(
        "--allow",
        nargs="*",
        default=[],
        metavar="PLUGINID",
        help="Plugin IDs exempt from the gate. Every one needs a written reason.",
    )
    args = parser.parse_args(argv)

    try:
        alerts = load_alerts(args.report)
    except ReportUnreadable as exc:
        print(f"::error::DAST gate cannot read the scan result: {exc}")
        print("::error::Refusing to pass. A gate that cannot see is not a gate.")
        return 1

    threshold = THRESHOLDS[args.min_risk]
    allowed = {str(plugin_id) for plugin_id in args.allow}

    over, suppressed = [], []
    for alert in alerts:
        if risk_of(alert) < threshold:
            continue
        if str(alert.get("pluginid")) in allowed:
            suppressed.append(alert)
        else:
            over.append(alert)

    print(f"DAST gate ({args.min_risk} and above): {census(alerts)}.")

    # Never silent: a suppression that leaves no trace in the log is how an
    # allowlist entry outlives the reason it was added.
    for alert in suppressed:
        print(f"::warning::SUPPRESSED by allowlist — {describe(alert).strip()}")

    if not over:
        print(f"No unsuppressed alerts at {args.min_risk} or above.")
        return 0

    print(f"::error::DAST gate: {len(over)} alert(s) at {args.min_risk} or above.")
    for alert in sorted(over, key=risk_of, reverse=True):
        print(describe(alert))
    print(
        "::error::Fix the finding, or add a justified entry to "
        "ZAP_GATE_ALLOWED_PLUGIN_IDS in .github/workflows/ci-dast.yml."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
