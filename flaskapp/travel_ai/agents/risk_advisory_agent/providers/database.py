"""v1's only `RiskDataProvider`: reads the three reference tables via
`flaskapp.database` — SQLite locally, the same schema on Postgres/Supabase
once `DATABASE_URL` is set, with no code change here either way (that
dual-mode handling already lives in `database.py`, not duplicated here).
"""

from __future__ import annotations

from pathlib import Path

from flaskapp.database import (
    get_risk_dated_events,
    get_risk_seasonal_windows,
    get_risk_standing_facts,
)
from flaskapp.travel_ai.agents.risk_advisory_agent.providers.base import RiskFetchResult
from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskProposalRequest


class DatabaseRiskProvider:
    name = "database"

    def __init__(self, database_path: Path | str):
        self._path = database_path

    def covers(self, request: RiskProposalRequest) -> bool:
        """True only when the destination has at least one standing fact.

        Checking standing facts specifically, not the other two tables: every
        seeded city has standing facts, but a city could legitimately have no
        seasonal window or no dated event in range without that meaning "we
        have no reference data for this destination at all."
        """
        if not request.destination_slug:
            return False
        return bool(get_risk_standing_facts(self._path, request.destination_slug))

    def fetch(self, request: RiskProposalRequest) -> RiskFetchResult:
        if not request.destination_slug:
            return RiskFetchResult(notes=["No destination slug resolved for this request."])
        slug = request.destination_slug
        return RiskFetchResult(
            standing_facts=get_risk_standing_facts(self._path, slug),
            seasonal_windows=get_risk_seasonal_windows(self._path, slug),
            dated_events=get_risk_dated_events(
                self._path, slug, str(request.departure_date), str(request.return_date)
            ),
            trust_level="authored",
        )
