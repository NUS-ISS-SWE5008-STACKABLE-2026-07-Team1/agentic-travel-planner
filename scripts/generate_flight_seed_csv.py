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
from datetime import date, datetime, timedelta
from pathlib import Path

RNG = random.Random(42)
# City routes were added after the original 100 rows and draw from their own
# stream. Sharing `RNG` would shift every subsequent draw and rewrite all 100
# existing rows on the next regeneration — a diff no reviewer could check, and
# a silent change to data other tests read.
CITY_RNG = random.Random(2026)
# Third stream, same reasoning again: the London hub was added after the SIN
# routes and must not shift a single existing row when the CSV is regenerated.
HUB_RNG = random.Random(7)

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

# --- Second hub: London -----------------------------------------------------
#
# Everything above is SIN-origin: every row has Singapore at one end. That shape,
# not the row count, is what forces every non-Singapore trip onto the unbacked
# prompt-only path — a traveller flying Paris to Rome has no data at all, and no
# amount of re-searching invents rows that do not exist.
#
# Chasing coverage is not the answer: `places.py` has 254 cities, which is 64,262
# ordered pairs and roughly 8.1M rows at this density. The dataset is a test
# fixture, not a product database. Adding a SECOND HUB changes its shape from one
# hub to two, which is the qualitative unlock; going from 280 to 2,800 rows of the
# same shape would not be.
#
# London, because `places.py` already resolves LHR/LGW/STN/LTN, SIN-LHR is
# already stocked (so the two hubs connect), and `scripts/duffel_smoke.py` already
# uses LHR-JFK as its reference search.
#
# Two rules, both load-bearing:
#
# 1. **Only NEW airport pairs.** `domain._matches_route` filters by route before
#    ranking, so an LHR-CDG row can never enter a SIN-NRT search. Adding a row to
#    an EXISTING SIN route would reorder a golden scenario and break
#    `test_flight_golden.py`.
# 2. **Deliberately incomplete.** Plenty of pairs stay unstocked on purpose. The
#    prompt-only path must remain reachable, or the regression tests covering it
#    (`test_flight_path2_screening.py`) would have nothing to exercise.
HUB_ORIGIN = "LHR"
HUB_ORIGIN_OFFSET = "+01:00"

# (dest_airport, dest_offset, direct_duration_min, [(carrier, tier), ...])
HUB_ROUTES = [
    ("CDG", "+02:00", 75, [("BA", "full"), ("AF", "full")]),
    ("AMS", "+02:00", 70, [("BA", "full"), ("KL", "full")]),
    ("FRA", "+02:00", 95, [("BA", "full"), ("LH", "full")]),
    ("MAD", "+02:00", 140, [("BA", "full"), ("IB", "full")]),
    ("BCN", "+02:00", 130, [("BA", "full"), ("VY", "budget")]),
    ("FCO", "+02:00", 155, [("BA", "full"), ("AZ", "full")]),
    ("MXP", "+02:00", 120, [("BA", "full"), ("AZ", "full"), ("U2", "budget")]),
    ("DUB", "+01:00", 85, [("BA", "full"), ("EI", "full")]),
    ("LIS", "+01:00", 165, [("BA", "full"), ("TP", "full")]),
    ("ZRH", "+02:00", 100, [("BA", "full"), ("LX", "full")]),
    ("CPH", "+02:00", 115, [("BA", "full"), ("SK", "full")]),
    ("JFK", "-04:00", 420, [("BA", "full"), ("VS", "full")]),
]

# Four departures per spoke, spread across the dataset's 2026-08-24 -> 2026-10-08
# window and deliberately NOT consecutive. The gaps are the point: a traveller
# asking for a date between them gets an empty leg on the single-shot path, which
# is exactly the case the tool loop's date widening improves.
HUB_TRIP_PLANS = {
    "CDG": [("2026-08-26", 4), ("2026-09-08", 3), ("2026-09-19", 5), ("2026-10-01", 4)],
    "AMS": [("2026-08-27", 3), ("2026-09-09", 4), ("2026-09-21", 3), ("2026-10-02", 4)],
    "FRA": [("2026-08-28", 4), ("2026-09-11", 3), ("2026-09-22", 4), ("2026-10-03", 3)],
    "MAD": [("2026-08-29", 5), ("2026-09-12", 6), ("2026-09-23", 5), ("2026-10-01", 6)],
    "BCN": [("2026-08-30", 4), ("2026-09-13", 5), ("2026-09-24", 4), ("2026-10-02", 5)],
    "FCO": [("2026-08-25", 6), ("2026-09-10", 5), ("2026-09-20", 6), ("2026-09-30", 5)],
    "MXP": [("2026-08-31", 4), ("2026-09-14", 3), ("2026-09-25", 4), ("2026-10-04", 3)],
    "DUB": [("2026-08-26", 3), ("2026-09-07", 2), ("2026-09-18", 3), ("2026-10-05", 2)],
    "LIS": [("2026-09-01", 6), ("2026-09-15", 5), ("2026-09-26", 6), ("2026-10-02", 5)],
    "ZRH": [("2026-08-27", 4), ("2026-09-16", 3), ("2026-09-27", 4), ("2026-10-03", 3)],
    "CPH": [("2026-09-02", 5), ("2026-09-17", 4), ("2026-09-28", 5), ("2026-10-04", 3)],
    "JFK": [("2026-08-24", 7), ("2026-09-06", 8), ("2026-09-19", 7), ("2026-09-29", 8)],
}

# Third flight-number range, above the city routes' 600, so the three streams can
# never mint the same flight_id.
HUB_FLIGHT_NO_BASE = 900

# --- Calendar extension: 2026-10-09 -> 2026-12-31 ----------------------------
#
# Everything above stops at 2026-10-08. That made any search past early October
# fall off the end of the dataset entirely — not a deliberate gap between stocked
# dates, just the edge of the world — so the whole Q4 of the demo calendar
# behaved like an unstocked route.
#
# This block re-flies the SAME routes across the rest of the year. It adds no new
# airport pairs: `SEED_ROUTES` and `covers_route()` are unchanged by it, so no
# trip that was previously unroutable becomes routable, and the prompt-only path
# (`test_flight_path2_screening.py`) keeps exactly the coverage it had.
#
# Fourth RNG stream and a fourth flight-number range, for the reason the three
# above have their own: appending must not shift a single one of the 472 existing
# rows when the CSV is regenerated. The bases start at 4000 because the hub
# stream's counters already climb into the 1300s (BA), and two streams that
# overlap would mint colliding flight_ids.
EXT_RNG = random.Random(1231)
EXT_SIN_FLIGHT_NO_BASE = 4000
EXT_CITY_FLIGHT_NO_BASE = 6000
EXT_HUB_FLIGHT_NO_BASE = 8000

EXT_START = date(2026, 10, 9)
EXT_END = date(2026, 12, 31)

# Irregular on purpose. A fixed 9-day step would make "is this date stocked?"
# predictable modulo 9; the varying cycle keeps the gaps between stocked dates
# uneven, which is what the tool loop's date widening actually has to cope with.
# Average ~9.2 days matches the density of the Aug-Oct rows above.
EXT_STEP_CYCLE = (8, 9, 11, 8, 10, 9)

# Departures are capped at EXT_END, but a round trip that departs 28 December
# has to come home in January — return legs are allowed to spill into early 2027
# rather than leaving late-December departures with no way back.

# Christmas/New Year peak. Fares rise and seats tighten, which is the only part
# of the calendar where the budget-renegotiation path gets exercised by the data
# itself rather than by a hand-written constraint.
PEAK_START = date(2026, 12, 18)
PEAK_END = date(2026, 12, 31)
PEAK_MULTIPLIER = 1.75
PEAK_MAX_SEATS = 6

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


def _is_peak(date_str: str) -> bool:
    """Whether a departure falls in the Christmas/New Year peak window.

    Deliberately derived from the date alone and consuming NO random draws:
    every existing row departs on or before 2026-10-08, so this returns False
    for all of them and the three original RNG streams advance exactly as they
    did before this window existed. A peak surcharge that drew from `rng` would
    have rewritten all 472 of them.
    """
    return PEAK_START <= datetime.strptime(date_str, "%Y-%m-%d").date() <= PEAK_END


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
        # Peak multiplier is applied to the price and the seat count, both after
        # their draws, so the peak window changes what the numbers ARE without
        # changing how many values come off the stream.
        peak = _is_peak(date_str)
        price = round(base_price * CABIN_MULTIPLIER[cabin] * (PEAK_MULTIPLIER if peak else 1.0), 0)
        seats = rng.choice([1, 2, 3, 4, 6, 8, 9, 12, 14, 18, 22, 30, 40]) if cabin == "ECONOMY" else rng.randint(1, 8)
        if peak:
            seats = min(seats, PEAK_MAX_SEATS)
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
    origin: str = ORIGIN, origin_offset: str = ORIGIN_OFFSET,
) -> list[dict]:
    """Both legs of one round trip. `origin` defaults to SIN so every existing
    call site is unchanged; the London hub passes its own."""
    return_date = (
        datetime.strptime(depart_date, "%Y-%m-%d") + timedelta(days=trip_len)
    ).strftime("%Y-%m-%d")
    return [
        *_generate_leg(
            origin, origin_offset, dest, dest_offset, depart_date,
            direct_duration, carriers, flight_no_counter, rng,
        ),
        *_generate_leg(
            dest, dest_offset, origin, origin_offset, return_date,
            direct_duration, carriers, flight_no_counter, rng,
        ),
    ]


def _ext_plans(
    dest: str,
    sources: tuple[dict[str, list[tuple[str, int]]], ...],
    origin: str,
    shares_dates_with: str | None = None,
) -> list[tuple[str, int]]:
    """(depart_date, trip_length) pairs from EXT_START to EXT_END for one route.

    Trip lengths are reused from the route's existing plans, so a Bangkok trip
    stays a long weekend and a London trip stays a fortnight in the extension
    window too. `sources` is passed in rather than searched across all three
    tables because a destination can appear in two of them with very different
    trip lengths — CDG is a 9-day holiday from Singapore and a 3-day hop from
    London, and picking whichever table matched first gave the hop the holiday.

    Departure dates are staggered per route so the routes do not all fly on the
    same dates, keyed on origin+destination for that same collision reason, and
    computed from the airport codes rather than drawn so the stagger stays stable
    when routes are added or reordered. Second airports pass `shares_dates_with`
    to inherit their primary's stagger — HND must keep flying on NRT's dates for
    the two to compete on a Tokyo search, as they do in the window above.
    """
    key = dest if shares_dates_with is None else shares_dates_with
    for plans in sources:
        if key in plans:
            lengths = [length for _date, length in plans[key]]
            break
    else:
        raise KeyError(f"no existing trip plan to derive lengths from: {origin}-{dest}")
    current = EXT_START + timedelta(days=sum(ord(c) for c in origin + key) % 9)
    plans: list[tuple[str, int]] = []
    index = 0
    while current <= EXT_END:
        plans.append((current.strftime("%Y-%m-%d"), lengths[index % len(lengths)]))
        current += timedelta(days=EXT_STEP_CYCLE[index % len(EXT_STEP_CYCLE)])
        index += 1
    return plans


def _extend_calendar(all_rows: list[dict]) -> None:
    """Re-fly every route above across 2026-10-09 -> 2026-12-31.

    Appended last, on its own RNG stream and flight-number ranges, so nothing
    already in `all_rows` is touched.
    """
    sin_counter: dict[str, int] = {}
    for dest, dest_offset, direct_duration, carriers in ROUTES:
        for carrier, _tier in carriers:
            sin_counter.setdefault(carrier, EXT_SIN_FLIGHT_NO_BASE)
        for depart_date, trip_len in _ext_plans(dest, (TRIP_PLANS,), ORIGIN):
            all_rows.extend(_round_trip(
                dest, dest_offset, direct_duration, carriers,
                depart_date, trip_len, sin_counter, EXT_RNG,
            ))

    city_counter: dict[str, int] = {}
    for dest, dest_offset, direct_duration, carriers, shares_dates_with in CITY_ROUTES:
        for carrier, _tier in carriers:
            city_counter.setdefault(carrier, EXT_CITY_FLIGHT_NO_BASE)
        for depart_date, trip_len in _ext_plans(
            dest, (CITY_TRIP_PLANS, TRIP_PLANS), ORIGIN, shares_dates_with,
        ):
            all_rows.extend(_round_trip(
                dest, dest_offset, direct_duration, carriers,
                depart_date, trip_len, city_counter, EXT_RNG,
            ))

    hub_counter: dict[str, int] = {}
    for dest, dest_offset, direct_duration, carriers in HUB_ROUTES:
        for carrier, _tier in carriers:
            hub_counter.setdefault(carrier, EXT_HUB_FLIGHT_NO_BASE)
        for depart_date, trip_len in _ext_plans(dest, (HUB_TRIP_PLANS,), HUB_ORIGIN):
            all_rows.extend(_round_trip(
                dest, dest_offset, direct_duration, carriers,
                depart_date, trip_len, hub_counter, EXT_RNG,
                origin=HUB_ORIGIN, origin_offset=HUB_ORIGIN_OFFSET,
            ))


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

    # London hub, appended last on its own RNG stream and flight-number range so
    # regenerating never rewrites a row above.
    hub_counter: dict[str, int] = {}
    for dest, dest_offset, direct_duration, carriers in HUB_ROUTES:
        for carrier, _tier in carriers:
            hub_counter.setdefault(carrier, HUB_FLIGHT_NO_BASE)
        for depart_date, trip_len in HUB_TRIP_PLANS[dest]:
            all_rows.extend(_round_trip(
                dest, dest_offset, direct_duration, carriers,
                depart_date, trip_len, hub_counter, HUB_RNG,
                origin=HUB_ORIGIN, origin_offset=HUB_ORIGIN_OFFSET,
            ))

    # Calendar extension last, for the same reason each block above it is last in
    # its turn: it must not shift a row written before it.
    _extend_calendar(all_rows)
    return all_rows


def main() -> None:
    rows = generate()

    # flight_id is the join key the agent quotes back and the tests match on, so
    # a collision between two streams would be a silent data corruption rather
    # than a visible failure. Four streams now mint ids independently; assert
    # rather than trust the flight-number ranges stay disjoint.
    ids = [row["flight_id"] for row in rows]
    if len(ids) != len(set(ids)):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise SystemExit(f"duplicate flight_id minted by overlapping streams: {duplicates}")

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
