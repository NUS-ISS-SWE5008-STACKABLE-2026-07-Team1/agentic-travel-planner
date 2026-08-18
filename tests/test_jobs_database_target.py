"""A DSN must reach save_plan intact.

Path("postgresql://host/db") normalises the double slash away, and the result
is no longer recognised as a DSN — so planning silently falls back to SQLite
while the web request path talks to Postgres. Nothing raises.
"""

from pathlib import Path

from flaskapp.database import is_postgres

DSN = "postgresql://user:pass@aws-0-us-west-2.pooler.supabase.com:5432/postgres"


def test_path_round_trip_destroys_a_dsn():
    """Characterises the bug this task fixes."""
    assert not is_postgres(str(Path(DSN)))


def test_service_keeps_a_dsn_usable():
    from flaskapp.travel_ai.service import TravelPlanningService

    service = TravelPlanningService(
        provider="openai", api_key="k", model="m", temperature=None,
        timeout=1, trace_dir=Path("instance/traces"), database_path=DSN,
    )
    assert is_postgres(service.database_path)
