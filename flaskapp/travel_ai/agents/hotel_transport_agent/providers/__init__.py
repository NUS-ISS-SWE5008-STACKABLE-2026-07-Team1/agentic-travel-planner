"""Hotel inventory providers and the factory that selects one.

Seed is the default and stays the default. The tests are pinned to that
dataset's exact ranking output, so switching sources is an explicit opt-in
rather than something a stray credential in the environment can trigger.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flaskapp.travel_ai.agents.hotel_transport_agent.providers.base import (
    HotelInventoryProvider,
    HotelResult,
)
from flaskapp.travel_ai.agents.hotel_transport_agent.providers.seed import (
    SeedHotelProvider,
)

__all__ = [
    "HotelInventoryProvider",
    "HotelResult",
    "SeedHotelProvider",
    "get_hotel_inventory_provider",
]


def get_hotel_inventory_provider(
    config: Mapping[str, Any],
) -> tuple[HotelInventoryProvider, str | None]:
    """The configured provider, plus a note if a requested one was unusable.

    Falls back to seed rather than failing when a live-source token is
    missing — a missing credential should degrade the data source, not take
    the planner down — but says so in the returned note so the fallback is
    visible instead of silent.
    """
    source = str(config.get("HOTEL_INVENTORY_SOURCE", "seed") or "seed").strip().lower()

    if source != "seed":
        return SeedHotelProvider(), (
            f"Unknown HOTEL_INVENTORY_SOURCE {source!r}; using the static hotel inventory."
        )

    return SeedHotelProvider(), None
