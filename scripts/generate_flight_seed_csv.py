"""One-off generator for flaskapp/travel_ai/agents/flight_agent/seed_data_extended.csv.

Run this to REGENERATE the CSV (deterministic — same output every run, seeded
RNG only jitters price/seat-count/minute-level timing within realistic
bounds, doesn't touch which routes/dates/carriers exist). The committed CSV
is static data loaded at import time, not regenerated at runtime — this
script is a dev tool for transparency/reproducibility of how the bulk
dataset was derived, not part of the shipped package.

Deliberately does NOT touch the 4 golden-scenario flights (SQ636/SQ632/
SQ637/SQ633) hardcoded in seed_data.py — several golden JSON scenarios and
unit tests hard-code their exact prices/times. This only adds rows.

Illustrative synthetic data (db_schema.md rule 3) — not real fares/schedules.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

RNG = random.Random(42)
# City routes were added after the original 100 rows and draw from their own
# stream. Sharing `RNG` would shift every subsequent draw and rewrite all 100
# existing rows on the next regeneration — a diff no reviewer could check, and
# a silent change to data other tests read.
CITY_RNG = random.Random(2026)

ORIGIN = "SIN"
ORIGIN_OFFSET = "+08:00"

# (dest_airport, dest_offset, direct_duration_min, [(carrier, tier), ...])
# tier "full" = full-service (SQ/NH/JL/BA/QF/CX-style); "budget" = LCC-style
# (TR/FD/JQ-style) — tier drives price band and step_free_boarding variety,
# not wheelchair_assist_available (that's a legal requirement, kept True
# almost everywhere, matching real-world obligations).
ROUTES = [
    ("NRT", "+09:00", 400, [("SQ", "full"), ("NH", "full"), ("JL", "full"), ("TR", "budget")]),
    ("LHR", "+01:00", 810, [("SQ", "full"), ("BA", "full")]),
    ("SYD", "+10:00", 480, [("SQ", "full"), ("QF", "full"), ("JQ", "budget")]),
    ("BKK", "+07:00", 140, [("SQ", "full"), ("TR", "budget"), ("FD", "budget")]),
    ("HKG", "+08:00", 220, [("SQ", "full"), ("CX", "full"), ("TR", "budget")]),
]

# (depart_date, trip_length_days) per route — short-haul gets shorter trips.
TRIP_PLANS = {
    "NRT": [("2026-08-28", 5), ("2026-09-10", 4), ("2026-09-15", 6), ("2026-09-20", 5), ("2026-09-25", 4)],
    "LHR": [("2026-08-25", 9), ("2026-09-05", 10), ("2026-09-14", 8), ("2026-09-21", 9), ("2026-09-28", 10)],
    "SYD": [("2026-08-27", 7), ("2026-09-04", 6), ("2026-09-11", 7), ("2026-09-18", 8), ("2026-09-24", 7)],
    "BKK": [("2026-08-29", 3), ("2026-09-06", 4), ("2026-09-13", 3), ("2026-09-19", 4), ("2026-09-27", 3)],
    "HKG": [("2026-08-30", 4), ("2026-09-08", 3), ("2026-09-16", 4), ("2026-09-22", 3), ("2026-09-29", 4)],
}

# --- City-level routes ------------------------------------------------------
# Added when the intake form moved from countries to cities. Two kinds:
#
# 1. **Second airports for cities already covered** (HND for Tokyo, LGW for
#    London, DMK for Bangkok). These deliberately reuse the PRIMARY airport's
#    departure dates so that a search for "Tokyo" on a given date finds both
#    NRT and HND options and the cheaper one can win. Without the shared dates
#    the multi-airport path would exist but never actually compete.
# 2. **New cities**, so a traveller picking Osaka or Seoul gets real results
#    rather than an empty flight section.
#
# Still SIN-origin hub-and-spoke, matching the existing dataset's shape.
CITY_ROUTES = [
    # Second airports for existing cities — dates shared with the primary.
    ("HND", "+09:00", 420, [("SQ", "full"), ("NH", "full"), ("JL", "full")], "NRT"),
    ("LGW", "+01:00", 810, [("SQ", "full"), ("BA", "full")], "LHR"),
    ("DMK", "+07:00", 140, [("TR", "budget"), ("FD", "budget")], "BKK"),
    # New cities.
    ("KIX", "+09:00", 390, [("SQ", "full"), ("TR", "budget")], None),
    ("MEL", "+10:00", 450, [("SQ", "full"), ("QF", "full"), ("JQ", "budget")], None),
    ("ICN", "+09:00", 380, [("SQ", "full"), ("KE", "full"), ("OZ", "full")], None),
    ("KUL", "+08:00", 60, [("SQ", "full"), ("MH", "full"), ("AK", "budget")], None),
    ("DPS", "+08:00", 165, [("SQ", "full"), ("GA", "full"), ("TR", "budget")], None),
    ("CGK", "+07:00", 105, [("SQ", "full"), ("GA", "full"), ("QG", "budget")], None),
    ("HKT", "+07:00", 105, [("SQ", "full"), ("TR", "budget"), ("FD", "budget")], None),
    ("TPE", "+08:00", 290, [("SQ", "full"), ("BR", "full"), ("CI", "full")], None),
    ("DXB", "+04:00", 440, [("SQ", "full"), ("EK", "full")], None),
    ("CDG", "+02:00", 800, [("SQ", "full"), ("AF", "full")], None),
]

# Departure dates for the NEW cities. Second airports reuse their primary's
# dates instead (see the `shares_dates_with` field above).
CITY_TRIP_PLANS = {
    "KIX": [("2026-08-28", 5), ("2026-09-10", 4), ("2026-09-20", 6)],
    "MEL": [("2026-08-27", 7), ("2026-09-11", 6), ("2026-09-24", 7)],
    "ICN": [("2026-08-29", 5), ("2026-09-12", 4), ("2026-09-23", 5)],
    "KUL": [("2026-08-26", 3), ("2026-09-07", 2), ("2026-09-18", 3)],
    "DPS": [("2026-08-30", 5), ("2026-09-09", 4), ("2026-09-26", 5)],
    "CGK": [("2026-08-31", 4), ("2026-09-14", 3), ("2026-09-25", 4)],
    "HKT": [("2026-08-29", 4), ("2026-09-08", 3), ("2026-09-19", 4)],
    "TPE": [("2026-08-27", 4), ("2026-09-13", 5), ("2026-09-22", 4)],
    "DXB": [("2026-08-25", 8), ("2026-09-06", 7), ("2026-09-21", 8)],
    "CDG": [("2026-08-24", 9), ("2026-09-05", 10), ("2026-09-27", 9)],
}

# New-route flight numbers start well above the originals so the two streams
# can never mint the same flight_id.
CITY_FLIGHT_NO_BASE = 600

CABINS_BY_TIER = {
    "full": ["ECONOMY", "ECONOMY", "PREMIUM_ECONOMY", "BUSINESS"],
    "budget": ["ECONOMY", "ECONOMY", "ECONOMY"],
}
PRICE_BAND = {  # (min, max) per 1000 duration_min, scaled per cabin below
    "full": (0.55, 0.85),
    "budget": (0.30, 0.45),
}
CABIN_MULTIPLIER = {"ECONOMY": 1.0, "PREMIUM_ECONOMY": 1.6, "BUSINESS": 3.2}

DEPARTURE_HOURS = [7, 9, 13, 17, 21, 23]


def _fmt_ts(date_str: str, hour: int, minute: int, offset: str) -> tuple[str, datetime]:
    naive = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")
    return f"{naive.strftime('%Y-%m-%dT%H:%M')}{offset}", naive


def _offset_minutes(offset: str) -> int:
    sign = 1 if offset[0] == "+" else -1
    hh, mm = offset[1:].split(":")
    return sign * (int(hh) * 60 + int(mm))


def _add_minutes(date_str: str, hour: int, minute: int, origin_offset: str, dest_offset: str, duration_min: int) -> str:
    _, naive_dep = _fmt_ts(date_str, hour, minute, origin_offset)
    dep_utc = naive_dep - timedelta(minutes=_offset_minutes(origin_offset))
    arr_utc = dep_utc + timedelta(minutes=duration_min)
    arr_local = arr_utc + timedelta(minutes=_offset_minutes(dest_offset))
    return f"{arr_local.strftime('%Y-%m-%dT%H:%M')}{dest_offset}"


def _generate_leg(
    origin: str, origin_offset: str, dest: str, dest_offset: str,
    date_str: str, direct_duration: int, carriers: list[tuple[str, str]],
    flight_no_counter: dict[str, int],
    rng: random.Random = RNG,
) -> list[dict]:
    """Rows for one leg. `rng` defaults to the original stream so the existing
    100 rows regenerate byte-identically; city routes pass `CITY_RNG`."""
    rows = []
    chosen_carriers = rng.sample(carriers, k=2)
    for carrier, tier in chosen_carriers:
        flight_no_counter[carrier] = flight_no_counter.get(carrier, 100) + rng.randint(1, 9)
        flight_no = f"{carrier}{flight_no_counter[carrier]}"
        hour = rng.choice(DEPARTURE_HOURS)
        minute = rng.choice([0, 5, 10, 15, 20, 30, 40, 45, 50])
        stops = 1 if (tier == "budget" and rng.random() < 0.35) else 0
        duration = direct_duration + (rng.randint(90, 240) if stops else rng.randint(-15, 20))
        dep_ts, _ = _fmt_ts(date_str, hour, minute, origin_offset)
        arr_ts = _add_minutes(date_str, hour, minute, origin_offset, dest_offset, duration)
        cabin = rng.choice(CABINS_BY_TIER[tier])
        band_lo, band_hi = PRICE_BAND[tier]
        base_price = direct_duration * rng.uniform(band_lo, band_hi)
        price = round(base_price * CABIN_MULTIPLIER[cabin], 0)
        seats = rng.choice([1, 2, 3, 4, 6, 8, 9, 12, 14, 18, 22, 30, 40]) if cabin == "ECONOMY" else rng.randint(1, 8)
        wheelchair = rng.random() > 0.05  # near-always True, small illustrative variance
        step_free = tier == "full" or rng.random() > 0.4
        date_compact = date_str.replace("-", "")

        # Seat inventory, correlated with total seats so it reads plausibly.
        # window/aisle roughly a third each; adjacency usually decent but
        # sometimes fragmented (drives the family-split scenario); accessible
        # seats scarce (drives the wheelchair scenario). Fees scale with tier.
        window_avail = max(0, seats // 3)
        aisle_avail = max(0, seats // 3)
        max_adjacent = rng.choice([1, 2, 2, 3, min(seats, 4), min(seats, 5)]) if seats > 0 else 0
        accessible_avail = rng.choice([0, 0, 1, 1, 2]) if wheelchair else 0
        std_fee = round(rng.choice([0, 8, 12, 15, 20]) * (1.5 if tier == "budget" else 1.0), 0)
        xleg_avail = rng.choice([0, 2, 4, 6])
        xleg_fee = round(rng.uniform(35, 70) * (1.4 if tier == "budget" else 1.0), 0)
        exit_avail = rng.choice([0, 2, 4])
        exit_fee = round(rng.uniform(45, 90) * (1.4 if tier == "budget" else 1.0), 0)

        rows.append({
            "flight_id": f"{flight_no}-{date_compact}",
            "carrier": carrier,
            "flight_no": flight_no,
            "origin_airport": origin,
            "dest_airport": dest,
            "dep_ts": dep_ts,
            "arr_ts": arr_ts,
            "duration_min": duration,
            "price": price,
            "cabin_class": cabin,
            "seats_available": seats,
            "stops": stops,
            "wheelchair_assist_available": wheelchair,
            "step_free_boarding": step_free,
            "seat_window_available": window_avail,
            "seat_aisle_available": aisle_avail,
            "seat_max_adjacent_block": max_adjacent,
            "seat_accessible_available": accessible_avail,
            "seat_standard_fee": std_fee,
            "seat_extra_legroom_available": xleg_avail,
            "seat_extra_legroom_fee": xleg_fee,
            "seat_exit_row_available": exit_avail,
            "seat_exit_row_fee": exit_fee,
        })
    return rows


def _round_trip(
    dest: str, dest_offset: str, direct_duration: int, carriers: list[tuple[str, str]],
    depart_date: str, trip_len: int, flight_no_counter: dict[str, int],
    rng: random.Random,
) -> list[dict]:
    return_date = (
        datetime.strptime(depart_date, "%Y-%m-%d") + timedelta(days=trip_len)
    ).strftime("%Y-%m-%d")
    return [
        *_generate_leg(
            ORIGIN, ORIGIN_OFFSET, dest, dest_offset, depart_date,
            direct_duration, carriers, flight_no_counter, rng,
        ),
        *_generate_leg(
            dest, dest_offset, ORIGIN, ORIGIN_OFFSET, return_date,
            direct_duration, carriers, flight_no_counter, rng,
        ),
    ]


def generate() -> list[dict]:
    all_rows: list[dict] = []
    flight_no_counter: dict[str, int] = {}
    for dest, dest_offset, direct_duration, carriers in ROUTES:
        for depart_date, trip_len in TRIP_PLANS[dest]:
            all_rows.extend(_round_trip(
                dest, dest_offset, direct_duration, carriers,
                depart_date, trip_len, flight_no_counter, RNG,
            ))

    # City routes are appended AFTER the originals, on their own RNG stream and
    # their own flight-number range, so regenerating never rewrites a row above.
    city_counter: dict[str, int] = {}
    for dest, dest_offset, direct_duration, carriers, shares_dates_with in CITY_ROUTES:
        # A second airport for a city already covered flies on the SAME dates as
        # its primary, so both compete on a given search instead of merely
        # existing in the dataset.
        plans = CITY_TRIP_PLANS.get(dest) or TRIP_PLANS[shares_dates_with]
        for carrier, _tier in carriers:
            city_counter.setdefault(carrier, CITY_FLIGHT_NO_BASE)
        for depart_date, trip_len in plans:
            all_rows.extend(_round_trip(
                dest, dest_offset, direct_duration, carriers,
                depart_date, trip_len, city_counter, CITY_RNG,
            ))
    return all_rows


def main() -> None:
    rows = generate()
    out_path = (
        Path(__file__).parent.parent
        / "flaskapp" / "travel_ai" / "agents" / "flight_agent" / "seed_data_extended.csv"
    )
    fieldnames = [
        "flight_id", "carrier", "flight_no", "origin_airport", "dest_airport",
        "dep_ts", "arr_ts", "duration_min", "price", "cabin_class",
        "seats_available", "stops", "wheelchair_assist_available", "step_free_boarding",
        "seat_window_available", "seat_aisle_available", "seat_max_adjacent_block",
        "seat_accessible_available", "seat_standard_fee", "seat_extra_legroom_available",
        "seat_extra_legroom_fee", "seat_exit_row_available", "seat_exit_row_fee",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
