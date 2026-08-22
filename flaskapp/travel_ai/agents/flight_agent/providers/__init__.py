"""Flight inventory providers and the factory that selects one.

Seed is the default and stays the default. The golden scenarios, the bias
audit, and ~100 tests are pinned to that dataset's exact ranking output, so
switching sources is an explicit opt-in (`FLIGHT_INVENTORY_SOURCE=duffel`)
rather than something a stray credential in the environment can trigger.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flaskapp.travel_ai.agents.flight_agent.providers.base import (
    InventoryProvider,
    InventoryResult,
)
from flaskapp.travel_ai.agents.flight_agent.providers.seed import SeedInventoryProvider

__all__ = [
    "InventoryProvider",
    "InventoryResult",
    "SeedInventoryProvider",
    "get_inventory_provider",
]


def get_inventory_provider(config: Mapping[str, Any]) -> tuple[InventoryProvider, str | None]:
    """The configured provider, plus a note if a requested one was unusable.

    Falls back to seed rather than failing when `FLIGHT_INVENTORY_SOURCE=duffel`
    is set without a token — a missing credential should degrade the data
    source, not take the planner down — but says so in the returned note so the
    fallback is visible instead of silent.
    """
    source = str(config.get("FLIGHT_INVENTORY_SOURCE", "seed") or "seed").strip().lower()

    if source == "duffel":
        token = config.get("DUFFEL_API_TOKEN")
        if not token:
            return SeedInventoryProvider(), (
                "FLIGHT_INVENTORY_SOURCE=duffel but DUFFEL_API_TOKEN is not set; "
                "using the static flight inventory instead."
            )
        # Imported lazily so the seed path never needs `requests` installed.
        from flaskapp.travel_ai.agents.flight_agent.providers.duffel import (
            DuffelInventoryProvider,
        )

        return DuffelInventoryProvider(
            token=token,
            api_version=str(config.get("DUFFEL_API_VERSION", "v2")),
            timeout_seconds=float(config.get("DUFFEL_TIMEOUT_SECONDS", 30)),
            supplier_timeout_ms=int(config.get("DUFFEL_SUPPLIER_TIMEOUT_MS", 20000)),
            max_offers=int(config.get("DUFFEL_MAX_OFFERS", 50)),
        ), None

    if source != "seed":
        return SeedInventoryProvider(), (
            f"Unknown FLIGHT_INVENTORY_SOURCE {source!r}; using the static flight inventory."
        )

    return SeedInventoryProvider(), None
