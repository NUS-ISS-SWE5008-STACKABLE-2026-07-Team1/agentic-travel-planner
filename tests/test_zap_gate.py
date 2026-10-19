"""The DAST risk gate, exercised without running a twenty-minute scan.

Being testable offline is the reason this gate is a script rather than ZAP's own
`exitStatus` job — see `scripts/zap_gate.py`. So these tests are not incidental
coverage; they are the argument for the design.

Reports are built as fixtures rather than committed as files. A real ZAP report
is ~200 KB of mostly solution text, and a fixture that fits on screen is one a
reader can check against the assertion.
"""

from __future__ import annotations

import json

import pytest

from scripts.zap_gate import main


def _alert(pluginid, riskcode, name="Some finding", instances=1):
    """One alert in ZAP traditional-json shape. riskcode is a STRING there."""
    return {
        "pluginid": str(pluginid),
        "riskcode": str(riskcode),
        "alert": name,
        "instances": [
            {"uri": f"http://127.0.0.1:5000/page{i}"} for i in range(instances)
        ],
    }


def _report(tmp_path, *alerts, sites=1):
    """Write a report whose alerts are spread over `sites` site entries."""
    buckets = [[] for _ in range(sites)]
    for index, alert in enumerate(alerts):
        buckets[index % sites].append(alert)
    path = tmp_path / "zap-authenticated-report.json"
    path.write_text(
        json.dumps({"site": [{"alerts": bucket} for bucket in buckets]}),
        encoding="utf-8",
    )
    return path


def test_a_clean_report_passes_and_still_prints_the_census(tmp_path, capsys):
    """The census prints on success too — that line is what makes a green DAST
    run mean something specific rather than just 'no error'."""
    report = _report(tmp_path, _alert(10036, 1, "Server leaks version"))

    assert main([str(report)]) == 0
    assert "1 Low" in capsys.readouterr().out


def test_a_medium_fails_and_names_itself(tmp_path, capsys):
    """A gate whose failure cannot be acted on from the log gets deleted."""
    report = _report(
        tmp_path, _alert(10038, 2, "Content Security Policy (CSP) Header Not Set", 5)
    )

    assert main([str(report)]) == 1
    out = capsys.readouterr().out
    assert "Content Security Policy" in out
    assert "10038" in out
    assert "5 instance(s)" in out
    assert "http://127.0.0.1:5000/page0" in out, "no example URI to start from"


def test_a_high_fails(tmp_path):
    report = _report(tmp_path, _alert(79, 3, "Cross Site Scripting (Reflected)"))

    assert main([str(report)]) == 1


def test_low_and_informational_do_not_fail_a_medium_gate(tmp_path):
    """The threshold is a decision, not an accident: today's release carries a
    Low (server version header) that is deliberately not being fixed."""
    report = _report(
        tmp_path,
        _alert(10036, 1, "Server leaks version"),
        _alert(10112, 0, "Session management response identified"),
    )

    assert main([str(report)]) == 0


def test_the_threshold_is_configurable(tmp_path):
    report = _report(tmp_path, _alert(10036, 1, "Server leaks version"))

    assert main([str(report), "--min-risk", "low"]) == 1
    assert main([str(report), "--min-risk", "medium"]) == 0


def test_an_allowlisted_finding_passes_but_says_so(tmp_path, capsys):
    """A suppression that leaves no trace is how an allowlist entry outlives the
    reason it was added."""
    report = _report(tmp_path, _alert(10038, 2, "CSP header not set"))

    assert main([str(report), "--allow", "10038"]) == 0
    out = capsys.readouterr().out
    assert "SUPPRESSED" in out
    assert "10038" in out


def test_allowlisting_one_finding_does_not_hide_another(tmp_path):
    report = _report(
        tmp_path,
        _alert(10038, 2, "CSP header not set"),
        _alert(79, 3, "Cross Site Scripting (Reflected)"),
    )

    assert main([str(report), "--allow", "10038"]) == 1


def test_a_false_positive_riskcode_is_not_read_as_a_risk(tmp_path):
    """ZAP writes riskcode "-1" for an alert an alertFilter marked a false
    positive. It is not a risk level, and `int("-1") < 2` would pass by accident
    rather than on purpose."""
    report = _report(tmp_path, _alert(10038, -1, "Marked false positive"))

    assert main([str(report)]) == 0


def test_alerts_are_counted_across_every_site(tmp_path):
    """A plan can scan more than one context, so alerts nest one level deeper
    than expected. Reading site[0] alone would silently miss findings."""
    report = _report(
        tmp_path,
        _alert(10036, 1, "Server leaks version"),
        _alert(79, 3, "Cross Site Scripting (Reflected)"),
        sites=2,
    )

    assert main([str(report)]) == 1


def test_a_missing_report_fails_closed(tmp_path, capsys):
    """The failure this gate exists to prevent is a scan that did not really
    happen reporting clean."""
    assert main([str(tmp_path / "nope.json")]) == 1
    assert "cannot read" in capsys.readouterr().out


def test_a_malformed_report_fails_closed(tmp_path, capsys):
    path = tmp_path / "zap-authenticated-report.json"
    path.write_text("{not json", encoding="utf-8")

    assert main([str(path)]) == 1
    assert "not valid JSON" in capsys.readouterr().out


def test_a_report_of_the_wrong_shape_fails_closed(tmp_path, capsys):
    """Valid JSON that is not a ZAP report — e.g. the wrong file matched by the
    workflow's `find` — must not be read as 'no alerts'."""
    path = tmp_path / "zap-authenticated-report.json"
    path.write_text('{"summary": "all good"}', encoding="utf-8")

    assert main([str(path)]) == 1
    assert "not a ZAP" in capsys.readouterr().out


def test_an_unparseable_riskcode_is_treated_as_the_worst_case(tmp_path):
    """If ZAP's format changes, the gate should go red and be looked at, not
    wave everything through."""
    report = _report(tmp_path, {"pluginid": "1", "riskcode": "high", "alert": "x"})

    assert main([str(report)]) == 1


@pytest.mark.parametrize("risk,expected", [(0, 0), (1, 0), (2, 1), (3, 1)])
def test_the_medium_threshold_is_where_it_says_it_is(tmp_path, risk, expected):
    report = _report(tmp_path, _alert(1, risk, "x"))

    assert main([str(report)]) == expected
