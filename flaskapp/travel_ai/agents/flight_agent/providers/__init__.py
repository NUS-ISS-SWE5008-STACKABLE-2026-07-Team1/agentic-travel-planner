"""Flight inventory providers and the factory that selects one.

Seed is the only source. The golden scenarios, the bias audit, and ~100 tests
are pinned to that dataset's exact ranking output. `InventoryProvider` stays a
Protocol so a live supplier can be added later without touching the node.
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

    An unknown `FLIGHT_INVENTORY_SOURCE` falls back to seed rather than failing
    — a bad setting should degrade the data source, not take the planner down —
    but says so in the returned note so the fallback is visible instead of
    silent.
    """
    source = str(config.get("FLIGHT_INVENTORY_SOURCE", "seed") or "seed").strip().lower()

    if source != "seed":
        return SeedInventoryProvider(), (
            f"Unknown FLIGHT_INVENTORY_SOURCE {source!r}; using the static flight inventory."
        )

    return SeedInventoryProvider(), None
