"""Covers the parts of `scripts/log_test_results.py` that need no database:
`parse_junit_xml`, and `main()`'s promise never to fail a CI job.

`log_run` itself runs against a real Postgres in
`tests/test_log_test_results_postgres.py`.
"""

from __future__ import annotations

from scripts.log_test_results import main, parse_junit_xml

JUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" errors="0" failures="1" skipped="1" tests="3" time="1.234">
<testcase classname="tests.test_example" name="test_pass" time="0.010"/>
<testcase classname="tests.test_example" name="test_fail" time="0.020">
<failure message="AssertionError: boom">Traceback (most recent call last):\nAssertionError: boom</failure>
</testcase>
<testcase classname="tests.test_example" name="test_skip" time="0.000">
<skipped message="not applicable here" type="pytest.skip"/>
</testcase>
</testsuite>
</testsuites>
"""


def _write(tmp_path, content: str) -> str:
    path = tmp_path / "report.xml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_parses_totals_and_duration(tmp_path):
    summary = parse_junit_xml(_write(tmp_path, JUNIT_XML))
    assert summary.total == 3
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.skipped == 1
    assert summary.duration_seconds == 1.234


def test_passed_case_has_no_failure_message(tmp_path):
    summary = parse_junit_xml(_write(tmp_path, JUNIT_XML))
    passed = next(r for r in summary.results if r.outcome == "passed")
    assert passed.name == "tests.test_example::test_pass"
    assert passed.duration_ms == 10
    assert passed.failure_message is None


def test_failed_case_captures_message_and_duration(tmp_path):
    summary = parse_junit_xml(_write(tmp_path, JUNIT_XML))
    failed = next(r for r in summary.results if r.outcome == "failed")
    assert failed.name == "tests.test_example::test_fail"
    assert failed.duration_ms == 20
    assert "AssertionError: boom" in failed.failure_message


def test_skipped_case_captures_the_skip_reason(tmp_path):
    """The `failure_message` column doubles as "why not a plain pass" —
    a skip reason is exactly as useful in history as a failure traceback."""
    summary = parse_junit_xml(_write(tmp_path, JUNIT_XML))
    skipped = next(r for r in summary.results if r.outcome == "skipped")
    assert skipped.name == "tests.test_example::test_skip"
    assert skipped.failure_message == "not applicable here"


def test_long_failure_message_is_truncated(tmp_path):
    huge = "x" * 5000
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" errors="0" failures="1" skipped="0" tests="1" time="0.1">
<testcase classname="tests.test_example" name="test_fail" time="0.1">
<failure message="boom">{huge}</failure>
</testcase>
</testsuite>
</testsuites>
"""
    summary = parse_junit_xml(_write(tmp_path, xml))
    [failed] = summary.results
    assert len(failed.failure_message) < len(huge)
    assert failed.failure_message.endswith("[truncated]")


def test_root_is_bare_testsuite_not_testsuites(tmp_path):
    """Some pytest versions/configs emit a bare <testsuite> root."""
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" errors="0" failures="0" skipped="0" tests="1" time="0.01">
<testcase classname="tests.test_example" name="test_pass" time="0.01"/>
</testsuite>
"""
    summary = parse_junit_xml(_write(tmp_path, xml))
    assert summary.total == 1
    assert summary.passed == 1


# --- main(): logging must never fail the build --------------------------------


def test_main_without_the_secret_prints_a_notice_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TEST_RESULTS_DATABASE_URL", raising=False)
    assert main([_write(tmp_path, JUNIT_XML), "--job", "tests"]) == 0
    assert "skipping test-result logging" in capsys.readouterr().out


def test_main_with_an_unreachable_database_still_exits_zero(tmp_path, monkeypatch, capsys):
    """Port 1 on localhost refuses immediately, standing in for a Supabase outage."""
    monkeypatch.setenv(
        "TEST_RESULTS_DATABASE_URL", "postgresql://nobody:nothing@127.0.0.1:1/none"
    )
    assert main([_write(tmp_path, JUNIT_XML), "--job", "tests"]) == 0
    out = capsys.readouterr().out
    assert "logging failed, build unaffected" in out
    # A connection error specifically. Without psycopg installed the import
    # fails instead and the same warning prints, so the first assertion alone
    # passes for the wrong reason.
    assert "connect" in out.lower()


def test_main_with_a_malformed_report_still_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(
        "TEST_RESULTS_DATABASE_URL", "postgresql://nobody:nothing@127.0.0.1:1/none"
    )
    assert main([_write(tmp_path, "<not-junit/>"), "--job", "tests"]) == 0
    assert "logging failed, build unaffected" in capsys.readouterr().out
