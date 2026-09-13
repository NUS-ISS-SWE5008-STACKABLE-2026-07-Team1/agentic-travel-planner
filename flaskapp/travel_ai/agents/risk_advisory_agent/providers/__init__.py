"""Provider selection. Mirrors `flight_agent.providers.get_inventory_provider`'s
shape exactly, including its return type — a factory has exactly one caller
per request lifecycle and returning `(provider, note)` lets that caller
surface a degraded-source note without a second round trip.

v1 has exactly one valid source. The branch for a future live source is not
written yet — that is the point of `RiskDataProvider` existing as an
interface at all: adding it later is a change inside this one function, not a
change to `domain.py`, `reasoning.py`, or `agent.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flaskapp.travel_ai.agents.risk_advisory_agent.providers.base import (
    RiskDataProvider,
    RiskFetchResult,
)
from flaskapp.travel_ai.agents.risk_advisory_agent.providers.database import DatabaseRiskProvider

__all__ = ["RiskDataProvider", "RiskFetchResult", "DatabaseRiskProvider", "get_risk_data_provider"]


def get_risk_data_provider(config: Mapping[str, Any]) -> tuple[RiskDataProvider, str | None]:
    source = str(config.get("RISK_DATA_SOURCE", "database") or "database").strip().lower()
    if source != "database":
        return DatabaseRiskProvider(config["DATABASE"]), (
            f"Unknown RISK_DATA_SOURCE {source!r}; using the database-backed reference data."
        )
    return DatabaseRiskProvider(config["DATABASE"]), None
