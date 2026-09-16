"""Provider selection. Mirrors `flight_agent.providers.get_inventory_provider`'s
shape: a factory returning `(provider, note)`, so a caller can surface a
degraded-source note without a second round trip.

v1 has exactly one valid source, and it needs no configuration — CSV data
loaded at import time, not a live connection. The branch for a future live
source is not written yet — that is the point of `RiskDataProvider` existing
as an interface at all: adding it later is a change inside this one
function, not a change to `domain.py`, `reasoning.py`, or `agent.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flaskapp.travel_ai.agents.risk_advisory_agent.providers.base import (
    RiskDataProvider,
    RiskFetchResult,
)
from flaskapp.travel_ai.agents.risk_advisory_agent.providers.seed import SeedRiskProvider

__all__ = ["RiskDataProvider", "RiskFetchResult", "SeedRiskProvider", "get_risk_data_provider"]


def get_risk_data_provider(config: Mapping[str, Any]) -> tuple[RiskDataProvider, str | None]:
    source = str(config.get("RISK_DATA_SOURCE", "seed") or "seed").strip().lower()
    if source != "seed":
        return SeedRiskProvider(), f"Unknown RISK_DATA_SOURCE {source!r}; using the seed reference data."
    return SeedRiskProvider(), None
