"""One live Duffel search, mapped and printed. No LLM, no graph.

The point is to confirm the two things a fixture cannot: that the credential
and headers are accepted, and that the *live* payload still maps the way
tests/fixtures/duffel_offer_request.json says it does. Duffel changes its
schema over time; this is what catches that.

Defaults to LHR->JFK for one adult, because that is the search Duffel documents
its sandbox airline (Duffel Airways, IATA ZZ) as reliably serving. Other real
airlines' sandboxes are external to Duffel and are frequently empty or down, so
an unfamiliar route returning zero offers usually says nothing about this code.
Expect unrealistic prices and schedules in test mode — that is documented Duffel
behaviour, not a mapping bug.

Needs DUFFEL_API_TOKEN in .env.secrets. Usage:
    python scripts/duffel_smoke.py
    python scripts/duffel_smoke.py --origin SIN --destination NRT --depart 2026-09-01
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flaskapp.config import Config
from flaskapp.travel_ai.agents.flight_agent.providers.duffel import DuffelInventoryProvider
from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposalRequest, TripContext


def _parse_args() -> argparse.Namespace:
    soon = date.today() + timedelta(days=30)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="LHR")
    parser.add_argument("--destination", default="JFK")
    parser.add_argument("--depart", default=soon.isoformat())
    parser.add_argument("--return-date", dest="return_date", default=(soon + timedelta(days=4)).isoformat())
    parser.add_argument("--ages", default="34", help="Comma-separated traveller ages.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    token = Config.DUFFEL_API_TOKEN
    if not token:
        print("DUFFEL_API_TOKEN is not set. Add it to .env.secrets and retry.")
        return 1

    # Never print the token itself — only enough to confirm which mode is live.
    mode = "test/sandbox" if token.startswith("duffel_test_") else "LIVE"
    print(f"Duffel mode: {mode}")
    print(f"Search: {args.origin} -> {args.destination}, out {args.depart}, back {args.return_date}\n")

    request = FlightProposalRequest(
        trip_context=TripContext(
            origin_airport=args.origin,
            dest_airport=args.destination,
            dest_country=args.destination,
            depart_date=args.depart,
            return_date=args.return_date,
            traveller_ages=[int(age) for age in args.ages.split(",") if age.strip()],
        )
    )

    provider = DuffelInventoryProvider(
        token=token,
        api_version=Config.DUFFEL_API_VERSION,
        timeout_seconds=Config.DUFFEL_TIMEOUT_SECONDS,
        supplier_timeout_ms=Config.DUFFEL_SUPPLIER_TIMEOUT_MS,
        max_offers=Config.DUFFEL_MAX_OFFERS,
    )
    result = provider.fetch(request)

    for note in result.notes:
        print(f"note: {note}")
    print(f"\nMapped {len(result.items)} leg(s).\n")

    for item in result.items:
        assist = "unverified" if item.wheelchair_assist_available is None else item.wheelchair_assist_available
        print(f"  {item.flight_id}")
        print(f"    {item.flight_no}  {item.origin_airport}->{item.dest_airport}  {item.stops} stop(s)")
        print(f"    dep {item.dep_ts}")
        print(f"    arr {item.arr_ts}")
        print(f"    {item.duration_min} min  {item.price:.2f}/pax/leg  {item.cabin_class}")
        print(f"    wheelchair assist: {assist}\n")

    # A UTC offset on both timestamps is the one mapping detail a fixture can
    # go stale on silently — if Duffel drops `time_zone` from its place objects,
    # every arrival-time constraint downstream quietly starts comparing naive
    # local clocks across timezones.
    naive = [i.flight_id for i in result.items if "+" not in i.dep_ts[10:] and "-" not in i.dep_ts[10:]]
    if naive:
        print(f"WARNING: {len(naive)} leg(s) have no UTC offset on dep_ts — check Duffel's `time_zone` field.")

    return 0 if result.items else 2


if __name__ == "__main__":
    raise SystemExit(main())
