"""Seed rows for Hotel & Transport Agent.

`SEED_HOTEL_INVENTORY` is loaded from `hotel_seed_data.csv` — a curated set of
hotels across the 16 cities that have flight inventory in the seed dataset.
Keyed on `city_slug` (e.g. `jp-tokyo`), never on the display name, in line with
the handoff's contract.

Transport seed data is embedded as Python data below rather than a separate
CSV, because the transport set is small and static (one entry per city/airport
pair).
"""

from __future__ import annotations

import csv
from pathlib import Path

from flaskapp.travel_ai.agents.hotel_transport_agent.schemas import (
    HotelInventoryItem,
    TransportOption,
)

_TRUE_STRINGS = {"true", "1", "yes"}


def _bool_or_none(value: str) -> bool | None:
    stripped = value.strip().lower()
    if stripped in _TRUE_STRINGS:
        return True
    if stripped in {"false", "0", "no"}:
        return False
    return None


def _load_hotel_csv() -> list[HotelInventoryItem]:
    csv_path = Path(__file__).parent / "hotel_seed_data.csv"
    with csv_path.open(encoding="utf-8", newline="") as f:
        return [
            HotelInventoryItem(
                hotel_id=row["hotel_id"],
                name=row["name"],
                city_slug=row["city_slug"],
                star_rating=int(row["star_rating"]) if row["star_rating"] else None,
                price_per_night=float(row["price_per_night"]),
                room_type=row["room_type"],
                distance_to_center_km=float(row["distance_to_center_km"]) if row["distance_to_center_km"] else None,
                wheelchair_accessible=_bool_or_none(row["wheelchair_accessible"]),
                step_free_entrance=_bool_or_none(row["step_free_entrance"]),
                accessible_bathroom=_bool_or_none(row["accessible_bathroom"]),
                braille_signage=_bool_or_none(row["braille_signage"]),
                amenities=[a.strip() for a in row["amenities"].split("|") if a.strip()],
            )
            for row in csv.DictReader(f)
        ]


# Transport seed: common airport transfer options per city/airport pair.
# Keyed on (city_slug, arrival_airport). An empty list means no seed data.
_TRANSPORT_SEED: dict[tuple[str, str], list[TransportOption]] = {
    ("sg-singapore", "SIN"): [
        TransportOption(
            name="Changi Airport Taxi",
            mode="taxi",
            duration_minutes=25,
            distance_km=20.0,
            estimated_cost=25.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Wheelchair-accessible taxis available on request"],
            source="seed",
            assumptions=["Metered fare; surcharges may apply"],
        ),
        TransportOption(
            name="Changi Airport MRT",
            mode="train",
            duration_minutes=40,
            distance_km=22.0,
            estimated_cost=2.5,
            currency="SGD",
            frequency="every 8 minutes",
            accessibility_notes=["Step-free access at all stations"],
            source="seed",
            assumptions=["Final leg may require bus/taxi if hotel is not near MRT"],
        ),
    ],
    ("jp-tokyo", "NRT"): [
        TransportOption(
            name="Narita Express Train",
            mode="train",
            duration_minutes=60,
            distance_km=80.0,
            estimated_cost=130.0,
            currency="SGD",
            frequency="every 30 minutes",
            accessibility_notes=["Reserved seating; step-free boarding available"],
            source="seed",
            assumptions=["Requires seat reservation; accepts IC cards"],
        ),
        TransportOption(
            name="Tokyo Airport Limousine Bus",
            mode="bus",
            duration_minutes=90,
            distance_km=70.0,
            estimated_cost=35.0,
            currency="SGD",
            frequency="every 20 minutes",
            accessibility_notes=["Low-floor buses; luggage storage"],
            source="seed",
            assumptions=["Traffic-dependent duration"],
        ),
    ],
    ("jp-tokyo", "HND"): [
        TransportOption(
            name="Haneda Airport Monorail",
            mode="train",
            duration_minutes=20,
            distance_km=18.0,
            estimated_cost=50.0,
            currency="SGD",
            frequency="every 10 minutes",
            accessibility_notes=["Step-free access; elevators at all stations"],
            source="seed",
            assumptions=["Connects to JR Yamanote line for onward travel"],
        ),
        TransportOption(
            name="Haneda Airport Taxi",
            mode="taxi",
            duration_minutes=30,
            distance_km=20.0,
            estimated_cost=45.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Wheelchair-accessible taxis available"],
            source="seed",
            assumptions=["Fixed or metered fare depending on destination"],
        ),
    ],
    ("jp-osaka", "KIX"): [
        TransportOption(
            name="Kansai Airport Express",
            mode="train",
            duration_minutes=50,
            distance_km=50.0,
            estimated_cost=95.0,
            currency="SGD",
            frequency="every 15 minutes",
            accessibility_notes=["Step-free boarding; reserved seats"],
            source="seed",
            assumptions=["Direct to Namba/Shin-Osaka"],
        ),
        TransportOption(
            name="Kansai Airport Limousine Bus",
            mode="bus",
            duration_minutes=70,
            distance_km=55.0,
            estimated_cost=30.0,
            currency="SGD",
            frequency="every 30 minutes",
            accessibility_notes=["Low-floor buses"],
            source="seed",
            assumptions=["Hotel drop-off at major properties"],
        ),
    ],
    ("gb-london", "LHR"): [
        TransportOption(
            name="Heathrow Express",
            mode="train",
            duration_minutes=15,
            distance_km=25.0,
            estimated_cost=35.0,
            currency="SGD",
            frequency="every 15 minutes",
            accessibility_notes=["Step-free access; staff assistance available"],
            source="seed",
            assumptions=["To Paddington; Tube/bus needed for central London"],
        ),
        TransportOption(
            name="Heathrow Airport Taxi",
            mode="taxi",
            duration_minutes=45,
            distance_km=25.0,
            estimated_cost=80.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Wheelchair-accessible black cabs available"],
            source="seed",
            assumptions=["Black cab rates fixed for central London"],
        ),
    ],
    ("gb-london", "LGW"): [
        TransportOption(
            name="Gatwick Express",
            mode="train",
            duration_minutes=30,
            distance_km=45.0,
            estimated_cost=30.0,
            currency="SGD",
            frequency="every 15 minutes",
            accessibility_notes=["Step-free access; advance assistance booking"],
            source="seed",
            assumptions=["To Victoria; connect to Tube for onward travel"],
        ),
    ],
    ("au-sydney", "SYD"): [
        TransportOption(
            name="Sydney Airport Train",
            mode="train",
            duration_minutes=15,
            distance_km=10.0,
            estimated_cost=18.0,
            currency="SGD",
            frequency="every 10 minutes",
            accessibility_notes=["Step-free access; lifts at all stations"],
            source="seed",
            assumptions=["To Central Station; bus/taxi needed for some suburbs"],
        ),
        TransportOption(
            name="Sydney Airport Taxi",
            mode="taxi",
            duration_minutes=25,
            distance_km=12.0,
            estimated_cost=35.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis; pre-book accessible vehicles"],
            source="seed",
            assumptions=["Metered fare; airport surcharge applies"],
        ),
    ],
    ("au-melbourne", "MEL"): [
        TransportOption(
            name="Melbourne Airport Bus",
            mode="bus",
            duration_minutes=30,
            distance_km=25.0,
            estimated_cost=20.0,
            currency="SGD",
            frequency="every 20 minutes",
            accessibility_notes=["Low-floor buses"],
            source="seed",
            assumptions=["SkyBus to Southern Cross Station"],
        ),
        TransportOption(
            name="Melbourne Airport Taxi",
            mode="taxi",
            duration_minutes=35,
            distance_km=25.0,
            estimated_cost=55.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis"],
            source="seed",
            assumptions=["Metered fare; premium for airport zone"],
        ),
    ],
    ("th-bangkok", "BKK"): [
        TransportOption(
            name="Bangkok Airport Rail Link",
            mode="train",
            duration_minutes=30,
            distance_km=35.0,
            estimated_cost=12.0,
            currency="SGD",
            frequency="every 10 minutes",
            accessibility_notes=["Step-free access at some stations"],
            source="seed",
            assumptions=["To Phaya Thai; connect to BTS/MRT"],
        ),
        TransportOption(
            name="Bangkok Airport Taxi",
            mode="taxi",
            duration_minutes=50,
            distance_km=38.0,
            estimated_cost=18.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Metered taxis; pre-book for wheelchair access"],
            source="seed",
            assumptions=["Traffic-heavy; tolls extra"],
        ),
    ],
    ("th-bangkok", "DMK"): [
        TransportOption(
            name="Don Mueang Airport Taxi",
            mode="taxi",
            duration_minutes=60,
            distance_km=40.0,
            estimated_cost=15.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Metered taxis"],
            source="seed",
            assumptions=["Traffic-dependent; tolls extra"],
        ),
    ],
    ("th-phuket", "HKT"): [
        TransportOption(
            name="Phuket Airport Bus",
            mode="bus",
            duration_minutes=60,
            distance_km=35.0,
            estimated_cost=8.0,
            currency="SGD",
            frequency="every 30 minutes",
            accessibility_notes=["Low-floor buses"],
            source="seed",
            assumptions=["To Phuket Town; songthaews for final leg"],
        ),
        TransportOption(
            name="Phuket Airport Taxi",
            mode="taxi",
            duration_minutes=45,
            distance_km=35.0,
            estimated_cost=25.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis; pre-book accessible"],
            source="seed",
            assumptions=["Fixed or metered depending on destination"],
        ),
    ],
    ("cn-hong-kong", "HKG"): [
        TransportOption(
            name="Hong Kong Airport Express",
            mode="train",
            duration_minutes=25,
            distance_km=35.0,
            estimated_cost=22.0,
            currency="SGD",
            frequency="every 10 minutes",
            accessibility_notes=["Step-free access; luggage racks"],
            source="seed",
            assumptions=["To Hong Kong/Austin stations"],
        ),
        TransportOption(
            name="Hong Kong Airport Taxi",
            mode="taxi",
            duration_minutes=40,
            distance_km=35.0,
            estimated_cost=50.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Red taxis (urban); no accessible fleet by default"],
            source="seed",
            assumptions=["Cross-harbour surcharge; luggage surcharge"],
        ),
    ],
    ("kr-seoul", "ICN"): [
        TransportOption(
            name="Seoul Airport Express (AREX)",
            mode="train",
            duration_minutes=45,
            distance_km=60.0,
            estimated_cost=18.0,
            currency="SGD",
            frequency="every 15 minutes",
            accessibility_notes=["Step-free access; elevators at stations"],
            source="seed",
            assumptions=["Express to Seoul Station; connect to subway"],
        ),
        TransportOption(
            name="Seoul Airport Limousine Bus",
            mode="bus",
            duration_minutes=70,
            distance_km=60.0,
            estimated_cost=15.0,
            currency="SGD",
            frequency="every 20 minutes",
            accessibility_notes=["Low-floor buses; luggage storage"],
            source="seed",
            assumptions=["Hotel drop-off at major properties"],
        ),
    ],
    ("kr-seoul", "GMP"): [
        TransportOption(
            name="Gimpo Airport Taxi",
            mode="taxi",
            duration_minutes=40,
            distance_km=30.0,
            estimated_cost=22.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis"],
            source="seed",
            assumptions=["Closer to city centre than ICN"],
        ),
    ],
    ("my-kuala-lumpur", "KUL"): [
        TransportOption(
            name="KLIA Express Train",
            mode="train",
            duration_minutes=30,
            distance_km=60.0,
            estimated_cost=12.0,
            currency="SGD",
            frequency="every 15 minutes",
            accessibility_notes=["Step-free access"],
            source="seed",
            assumptions=["To KL Sentral; connect to monorail/LRT"],
        ),
        TransportOption(
            name="KLIA Airport Taxi",
            mode="taxi",
            duration_minutes=50,
            distance_km=60.0,
            estimated_cost=20.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis; pre-book accessible"],
            source="seed",
            assumptions=["Fixed coupon taxi from airport"],
        ),
    ],
    ("id-bali", "DPS"): [
        TransportOption(
            name="Bali Airport Taxi",
            mode="taxi",
            duration_minutes=30,
            distance_km=15.0,
            estimated_cost=12.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis; no dedicated accessible fleet"],
            source="seed",
            assumptions=["Blue Bird taxis are metered; others fixed-price"],
        ),
        TransportOption(
            name="Bali Airport Shuttle",
            mode="bus",
            duration_minutes=45,
            distance_km=20.0,
            estimated_cost=5.0,
            currency="SGD",
            frequency="every 30 minutes",
            accessibility_notes=["Shared shuttle; limited luggage space"],
            source="seed",
            assumptions=["Per person; drops at major hotel zones"],
        ),
    ],
    ("id-jakarta", "CGK"): [
        TransportOption(
            name="Jakarta Airport Train",
            mode="train",
            duration_minutes=45,
            distance_km=40.0,
            estimated_cost=8.0,
            currency="SGD",
            frequency="every 30 minutes",
            accessibility_notes=["Step-free access at stations"],
            source="seed",
            assumptions=["To Manggarai; connect to commuter line"],
        ),
        TransportOption(
            name="Jakarta Airport Taxi",
            mode="taxi",
            duration_minutes=60,
            distance_km=40.0,
            estimated_cost=15.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis; pre-book for wheelchair"],
            source="seed",
            assumptions=["Traffic-heavy; tolls extra"],
        ),
    ],
    ("tw-taipei", "TPE"): [
        TransportOption(
            name="Taipei Airport MRT",
            mode="train",
            duration_minutes=35,
            distance_km=40.0,
            estimated_cost=18.0,
            currency="SGD",
            frequency="every 10 minutes",
            accessibility_notes=["Step-free access; luggage racks"],
            source="seed",
            assumptions=["To Taipei Main Station; connect to subway"],
        ),
        TransportOption(
            name="Taipei Airport Taxi",
            mode="taxi",
            duration_minutes=50,
            distance_km=40.0,
            estimated_cost=30.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis"],
            source="seed",
            assumptions=["Metered fare; surcharge for large luggage"],
        ),
    ],
    ("ae-dubai", "DXB"): [
        TransportOption(
            name="Dubai Metro Red Line",
            mode="train",
            duration_minutes=25,
            distance_km=15.0,
            estimated_cost=6.0,
            currency="SGD",
            frequency="every 8 minutes",
            accessibility_notes=["Fully step-free; lifts and escalators everywhere"],
            source="seed",
            assumptions=["To Union/Deira City Centre stations"],
        ),
        TransportOption(
            name="Dubai Airport Taxi",
            mode="taxi",
            duration_minutes=30,
            distance_km=15.0,
            estimated_cost=20.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis; accessible taxis available by request"],
            source="seed",
            assumptions=["Metered fare; airport surcharge"],
        ),
    ],
    ("fr-paris", "CDG"): [
        TransportOption(
            name="Paris RER B Train",
            mode="train",
            duration_minutes=45,
            distance_km=30.0,
            estimated_cost=15.0,
            currency="SGD",
            frequency="every 6 minutes",
            accessibility_notes=["Step-free access at some stations only"],
            source="seed",
            assumptions=["To Gare du Nord; connect to Metro"],
        ),
        TransportOption(
            name="Paris Airport Taxi",
            mode="taxi",
            duration_minutes=50,
            distance_km=30.0,
            estimated_cost=60.0,
            currency="SGD",
            frequency="on_demand",
            accessibility_notes=["Standard taxis; pre-book for wheelchair access"],
            source="seed",
            assumptions=["Fixed rate to right bank; left bank higher"],
        ),
    ],
}


SEED_HOTEL_INVENTORY: list[HotelInventoryItem] = _load_hotel_csv()

SEED_TRANSPORT_OPTIONS: list[TransportOption] = [
    opt for opts in _TRANSPORT_SEED.values() for opt in opts
]

# Derived coverage sets.
SEED_HOTEL_CITIES: frozenset[str] = frozenset(
    item.city_slug for item in SEED_HOTEL_INVENTORY
)

SEED_TRANSPORT_PAIRS: frozenset[tuple[str, str]] = frozenset(_TRANSPORT_SEED.keys())


def covers_hotels(city_slugs: list[str] | tuple[str, ...]) -> bool:
    """Whether any of the given city slugs have hotel inventory."""
    return any(slug in SEED_HOTEL_CITIES for slug in city_slugs)


def covers_transport(city_slug: str, arrival_airport: str) -> bool:
    """Whether we have seed transport data for this city/airport pair."""
    return (city_slug, arrival_airport) in SEED_TRANSPORT_PAIRS


def transport_for(city_slug: str, arrival_airport: str) -> list[TransportOption]:
    """Seed transport options for a specific city/airport pair."""
    return list(_TRANSPORT_SEED.get((city_slug, arrival_airport), []))
