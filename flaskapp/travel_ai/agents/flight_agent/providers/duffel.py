"""Live flight inventory from the Duffel API.

No SDK. Duffel's official Python client (`duffelhq/duffel-api-python`) was
archived by Duffel on 2024-09-12 and is read-only, so depending on it would be
adopting an abandoned package for a credentialed network path. The REST surface
we need is one endpoint, so this calls it directly with `requests`.

    POST https://api.duffel.com/air/offer_requests?return_offers=true
    Authorization: Bearer <token>          # duffel_test_… or duffel_live_…
    Duffel-Version: v2

What Duffel does not give us, and what is done about it:

| Field `domain.py` needs | In a Duffel offer? | Handling |
|---|---|---|
| `wheelchair_assist_available` | No | `None` — unverified, never fabricated |
| `step_free_boarding` | No | `None` |
| `seats_available` | No | Party size, so it never falsely excludes; noted |
| `seat_inventory` | Only via a separate per-offer Seat Maps call | `None`; out of scope |
| per-leg `price` | No — one total for the whole trip | Divided; see `_leg_price` |

The shape mismatch worth understanding: Duffel prices a return trip as ONE
offer containing two slices, while this agent models legs independently and
recombines them. So each offer becomes one `FlightInventoryItem` per slice, and
the fare is split. That makes per-leg prices approximate — a real fare is not
half a round trip — which is why every Duffel row carries `source="duffel"` and
agent.py attaches a different assumption string than the seed path uses.

Nothing here raises for a data or network problem. A timeout, a 4xx, or a
malformed payload returns an empty `InventoryResult` with a note explaining
itself, and agent.py degrades to the prompt-only path exactly as it does for an
unstocked route.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from flaskapp.travel_ai.agents.flight_agent.providers.base import InventoryResult
from flaskapp.travel_ai.agents.flight_agent.schemas import (
    FlightInventoryItem,
    FlightProposalRequest,
)

API_URL = "https://api.duffel.com/air/offer_requests"

INVENTORY_ASSUMPTION = (
    "Fare and schedule come from a live Duffel supplier search and expire "
    "quickly; the price shown is the trip total apportioned across legs, not a "
    "quoted one-way fare. Seat availability and accessibility are not published "
    "by this feed. Confirm everything with the carrier before booking."
)

SEATS_NOTE = (
    "Duffel does not publish remaining seat counts, so seat-availability "
    "filtering was skipped for these options."
)
ACCESSIBILITY_NOTE = (
    "Duffel does not publish wheelchair assistance or step-free boarding, so "
    "those could not be verified for these options."
)

# Airports searched per city, per end of the trip. Each additional airport is
# another billed, rate-limited, latency-adding round trip, and the pairs
# multiply: London (4 airports) to Tokyo (2) would be 8 searches for one plan.
# Two covers the dominant share of real traffic at every multi-airport city in
# `places.py`, and the cap is disclosed to the traveller when it bites.
MAX_AIRPORTS_PER_CITY = 2


def _dedupe(notes: list[str]) -> list[str]:
    """Distinct notes, first occurrence order.

    Fanning out repeats the same per-pair note (no offers, unreachable) once
    per pair. Travellers should be told a thing once.
    """
    return list(dict.fromkeys(notes))

# ISO 8601 durations as Duffel emits them, e.g. "PT7H10M", "P1DT2H5M".
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?)?$"
)


def parse_iso_duration(value: str | None) -> int | None:
    """ISO 8601 duration -> whole minutes, or None if it cannot be read.

    Returns None rather than 0 on failure: `FlightInventoryItem.duration_min`
    is constrained `gt=0`, so a silent 0 would raise a validation error far
    from the malformed input that caused it. The caller falls back to computing
    the duration from the timestamps instead.
    """
    if not value:
        return None
    match = _DURATION_RE.match(value.strip())
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    total = parts["days"] * 1440 + parts["hours"] * 60 + parts["minutes"]
    return total or None


def _zone(place: dict | None) -> ZoneInfo | None:
    """The IANA zone for a Duffel place, if it published a usable one."""
    if not isinstance(place, dict):
        return None
    name = place.get("time_zone")
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def localise(timestamp: str, place: dict | None) -> str:
    """Duffel's local-but-naive timestamp -> ISO 8601 *with* a UTC offset.

    This is not cosmetic. `FlightInventoryItem` documents `dep_ts` as
    origin-local with offset and `arr_ts` as destination-local with offset, and
    `domain._is_red_eye` plus downstream check-in feasibility both read those
    offsets. Duffel sends `"2026-09-01T16:00:00"` with the zone carried
    separately on the place object, so the two have to be recombined here.

    A timestamp that already carries an offset is returned untouched, and one
    whose zone cannot be resolved is returned naive — degraded (local-clock
    comparisons still work) rather than wrong (which a guessed offset would be).
    """
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return timestamp
    if parsed.tzinfo is not None:
        return parsed.isoformat()
    zone = _zone(place)
    return parsed.replace(tzinfo=zone).isoformat() if zone else parsed.isoformat()


def _leg_price(offer: dict, slice_count: int, passenger_count: int) -> float:
    """Per-passenger, per-leg fare from Duffel's whole-trip total.

    `domain._effective_cost` multiplies `price` by party size, and candidates
    are ranked per leg, so the total has to be divided by both. Even division
    across legs is an approximation Duffel gives us no way to improve — it does
    not break out a per-slice fare — and it is why these rows are labelled as
    apportioned in `INVENTORY_ASSUMPTION`.
    """
    total = float(offer.get("total_amount") or 0)
    return total / max(slice_count, 1) / max(passenger_count, 1)


def _segment_carrier(segment: dict) -> tuple[str, str]:
    carrier = segment.get("marketing_carrier") or {}
    code = carrier.get("iata_code") or "??"
    number = segment.get("marketing_carrier_flight_number") or ""
    return code, f"{code}{number}"


def _cabin_class(segment: dict) -> str:
    passengers = segment.get("passengers") or []
    if passengers and isinstance(passengers[0], dict):
        return str(passengers[0].get("cabin_class") or "economy").upper()
    return "ECONOMY"


def slice_to_item(
    offer: dict, slice_: dict, index: int, *, passenger_count: int, slice_count: int
) -> FlightInventoryItem | None:
    """One Duffel slice -> one inventory row, or None if it is unusable.

    Multi-segment slices collapse to a single row spanning first departure to
    last arrival, with `stops` = segment count - 1 — which is exactly the shape
    `domain.py` filters and ranks on.
    """
    segments = slice_.get("segments") or []
    if not segments:
        return None

    first, last = segments[0], segments[-1]
    dep_ts = localise(first.get("departing_at"), first.get("origin"))
    arr_ts = localise(last.get("arriving_at"), last.get("destination"))

    duration_min = parse_iso_duration(slice_.get("duration"))
    if duration_min is None:
        try:
            elapsed = datetime.fromisoformat(arr_ts) - datetime.fromisoformat(dep_ts)
            duration_min = int(elapsed.total_seconds() // 60)
        except (TypeError, ValueError):
            return None
    if duration_min <= 0:
        return None

    price = _leg_price(offer, slice_count, passenger_count)
    if price <= 0:
        return None

    carrier, flight_no = _segment_carrier(first)
    origin = (slice_.get("origin") or {}).get("iata_code") or (first.get("origin") or {}).get("iata_code")
    dest = (slice_.get("destination") or {}).get("iata_code") or (last.get("destination") or {}).get("iata_code")
    if not origin or not dest:
        return None

    return FlightInventoryItem(
        # Offer id + slice index: unique, and traceable back to the bookable
        # offer. Unlike a seed id this is NOT stable across searches — Duffel
        # offers expire (`offer.expires_at`), so it identifies a quote, not a
        # flight.
        flight_id=f"{offer.get('id')}:{index}",
        carrier=carrier,
        flight_no=flight_no,
        origin_airport=origin,
        dest_airport=dest,
        dep_ts=dep_ts,
        arr_ts=arr_ts,
        duration_min=duration_min,
        price=price,
        cabin_class=_cabin_class(first),
        # Duffel publishes no remaining-seat count. Party size keeps
        # `domain._screen_item`'s seat check from excluding every live option
        # on data we simply do not have; SEATS_NOTE tells the traveller that.
        seats_available=passenger_count,
        stops=len(segments) - 1,
        wheelchair_assist_available=None,
        step_free_boarding=None,
        seat_inventory=None,
        source="duffel",
    )


def offers_to_inventory(payload: dict, *, passenger_count: int) -> list[FlightInventoryItem]:
    """Every slice of every offer in a Duffel offer-request response."""
    offers = ((payload or {}).get("data") or {}).get("offers") or []
    items: list[FlightInventoryItem] = []
    for offer in offers:
        slices = offer.get("slices") or []
        for index, slice_ in enumerate(slices):
            item = slice_to_item(
                offer, slice_, index,
                passenger_count=passenger_count, slice_count=len(slices),
            )
            if item is not None:
                items.append(item)
    return items


def build_payload(
    request: FlightProposalRequest,
    *,
    supplier_timeout_ms: int,
    origin: str | None = None,
    dest: str | None = None,
) -> dict:
    """The offer-request body for a return trip between two airports.

    `origin`/`dest` name the specific airport pair to search, because a city
    can have several and each pair is its own Duffel search. They default to
    the context's primary gateways so a caller that has not resolved a city
    still gets the old single-search behaviour.

    Passengers map one-to-one from `traveller_ages` — Duffel v2 takes an `age`
    directly, so no adult/child bucketing is needed and none is invented. A
    request with no ages recorded falls back to a single adult, matching
    `TripContext.party`'s own default.
    """
    ctx = request.trip_context
    ages = list(ctx.traveller_ages) or [30]
    origin = origin or ctx.origin_airport
    dest = dest or ctx.dest_airport
    return {
        "data": {
            "slices": [
                {
                    "origin": origin,
                    "destination": dest,
                    "departure_date": ctx.depart_date,
                },
                {
                    "origin": dest,
                    "destination": origin,
                    "departure_date": ctx.return_date,
                },
            ],
            "passengers": [{"age": age} for age in ages],
            "cabin_class": "economy",
            "supplier_timeout": supplier_timeout_ms,
        }
    }


class DuffelInventoryProvider:
    """Live inventory from one Duffel offer-request per proposal round."""

    name = "duffel"
    # Every fetch is a distinct billed, rate-limited supplier search, and one
    # fetch fans out over airport pairs. See `agents.loop.LoopBudget`.
    is_static = False
    assumption = INVENTORY_ASSUMPTION

    def __init__(
        self,
        token: str,
        *,
        api_version: str = "v2",
        timeout_seconds: float = 30.0,
        supplier_timeout_ms: int = 20000,
        max_offers: int = 50,
        session: requests.Session | None = None,
    ) -> None:
        self._token = token
        self._api_version = api_version
        self._timeout_seconds = timeout_seconds
        self._supplier_timeout_ms = supplier_timeout_ms
        self._max_offers = max_offers
        # Injectable so tests exercise the mapping against a fixture without
        # a token or a network.
        self._session = session or requests.Session()

    def covers(self, request: FlightProposalRequest) -> bool:
        """Routability only. Duffel has no fixed route list to check against,
        so anything with two resolved airports is worth asking about — an
        unserved city pair comes back as zero offers, which `fetch` reports as
        a note rather than a failure."""
        ctx = request.trip_context
        return bool(ctx.origin_airports and ctx.dest_airports)

    def _airport_pairs(self, request: FlightProposalRequest) -> tuple[list[tuple[str, str]], bool]:
        """Airport pairs to search, and whether the list was truncated.

        A city can have four airports (London), and every pair is a separate
        billed, rate-limited, latency-adding Duffel call — London to Tokyo is
        eight searches unbounded. Each end is capped at its two primary
        gateways, which covers the overwhelming majority of real traffic, and
        the truncation is returned so `fetch` can disclose it rather than
        quietly search less than the traveller asked for.
        """
        ctx = request.trip_context
        origins = list(ctx.origin_airports)[:MAX_AIRPORTS_PER_CITY]
        dests = list(ctx.dest_airports)[:MAX_AIRPORTS_PER_CITY]
        truncated = (
            len(ctx.origin_airports) > MAX_AIRPORTS_PER_CITY
            or len(ctx.dest_airports) > MAX_AIRPORTS_PER_CITY
        )
        return [(origin, dest) for origin in origins for dest in dests], truncated

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Duffel-Version": self._api_version,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def fetch(self, request: FlightProposalRequest) -> InventoryResult:
        """Search every airport pair for the chosen cities and merge the rows.

        One Duffel search only covers one airport pair, so a multi-airport city
        needs several. Rows are merged and de-duplicated on `flight_id`;
        `domain.py` then ranks the combined set exactly as it ranks seed rows,
        which is what lets a Haneda fare beat a Narita one on price.

        A failing pair does not fail the fetch. Its note is collected and the
        remaining pairs still contribute — a partial answer with a stated gap
        beats no answer at all.
        """
        if not self.covers(request):
            return InventoryResult(notes=["Could not resolve both airports for a Duffel search."])

        pairs, truncated = self._airport_pairs(request)
        passenger_count = len(request.trip_context.traveller_ages) or 1

        merged: dict[str, FlightInventoryItem] = {}
        notes: list[str] = []
        for origin, dest in pairs:
            result = self._search_pair(request, origin, dest, passenger_count=passenger_count)
            for item in result.items:
                merged.setdefault(item.flight_id, item)
            notes.extend(result.notes)

        if truncated:
            notes.append(
                f"Searched only the {MAX_AIRPORTS_PER_CITY} main airports at each end; "
                "other airports serving these cities were not checked."
            )

        if not merged:
            # Every pair came back empty or failed; their notes already say why.
            return InventoryResult(notes=_dedupe(notes))

        items = list(merged.values())[: self._max_offers]
        return InventoryResult(items=items, notes=_dedupe([SEATS_NOTE, ACCESSIBILITY_NOTE, *notes]))

    def _search_pair(
        self,
        request: FlightProposalRequest,
        origin: str,
        dest: str,
        *,
        passenger_count: int,
    ) -> InventoryResult:
        """One Duffel offer-request for one airport pair.

        Never raises: every failure path returns rows-free `InventoryResult`
        with a traveller-safe note, so one unreachable pair cannot take down a
        multi-airport search.
        """
        try:
            response = self._session.post(
                API_URL,
                params={"return_offers": "true"},
                json=build_payload(
                    request,
                    supplier_timeout_ms=self._supplier_timeout_ms,
                    origin=origin,
                    dest=dest,
                ),
                headers=self._headers,
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            # Deliberately the exception TYPE, never `str(exc)` — a requests
            # error message can embed the full request URL and headers, and
            # these notes are traveller-visible.
            return InventoryResult(
                notes=[
                    f"Live flight search for {origin}-{dest} could not be reached "
                    f"({type(exc).__name__})."
                ]
            )

        if response.status_code >= 400:
            return InventoryResult(
                notes=[
                    f"Live flight search for {origin}-{dest} failed with status "
                    f"{response.status_code}."
                ]
            )

        try:
            payload = response.json()
        except ValueError:
            return InventoryResult(
                notes=[f"Live flight search for {origin}-{dest} returned an unreadable response."]
            )

        items = offers_to_inventory(payload, passenger_count=passenger_count)
        if not items:
            return InventoryResult(
                notes=[f"The live flight search returned no offers for {origin}-{dest}."]
            )

        # Cap AFTER mapping so the limit counts legs consistently, and so a
        # partially malformed payload cannot squeeze good offers out.
        return InventoryResult(items=items[: self._max_offers])
