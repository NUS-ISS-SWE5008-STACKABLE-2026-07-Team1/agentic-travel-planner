"""Guards that keep database.py runnable on both engines.

A source-text guard is crude but it is the only check that fails at the moment
someone reintroduces an engine-specific function, rather than months later when
the deployed dashboard returns empty rows.
"""

from pathlib import Path

SOURCE = Path("flaskapp/database.py").read_text(encoding="utf-8")


def test_no_sqlite_only_date_functions():
    assert "STRFTIME" not in SOURCE.upper(), "use SUBSTR(x, 1, 7) for month grouping"
    assert "DATE(" not in SOURCE.upper().replace("UPDATE(", ""), (
        "use SUBSTR(x, 1, 10) for day grouping"
    )


def test_no_literal_percent_in_sql():
    """psycopg reads % as its own placeholder, so a literal % breaks binding."""
    assert "'%" not in SOURCE
