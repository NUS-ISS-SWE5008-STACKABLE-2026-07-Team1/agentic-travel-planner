# Cities and airports: the shared contract

**Audience:** the Hotel & Transport Agent developer, and anyone else building
inventory that has to line up with Flight Agent's.

Origin and destination are now collected as **country + city**, not country
alone. `flaskapp/places.py` is the single dataset behind that, shared by the
intake form, Flight Agent, and any agent whose inventory is located somewhere.
Build against this module rather than copying its data — a second copy is a
second thing to keep in sync, and they will diverge.

## Key your tables on the slug

```python
from flaskapp.places import CITIES, cities_for, city_by_slug, find_city

city = city_by_slug("jp-tokyo")
city.slug        # "jp-tokyo"  <- the stable key. Join on this.
city.name        # "Tokyo"     <- display only; may be corrected
city.country     # "Japan"     <- matches flaskapp/countries.py exactly
city.airports    # ("NRT", "HND")  ordered, primary first
city.primary_airport  # "NRT"
city.label       # "Tokyo (NRT/HND)"
```

**Use `slug` as your foreign key, never `name`.** The name is data that can be
corrected — a diacritic fix, a disambiguation like "Osaka" becoming "Osaka
(Kansai)" — and any table keyed on the display string breaks when that happens.
The slug is opaque: don't parse it, don't derive it from the name.

Today's dataset: **253 cities, 61 countries, 283 airports**, 24 of them
multi-airport. Only cities with a scheduled-service IATA airport are included,
which is what makes the city list and the airport map the same dataset.

### The functions you'll want

| Call | Returns |
|---|---|
| `cities_for("Japan")` | every selectable city in a country, alphabetical |
| `city_by_slug("jp-tokyo")` | one city, or `None` |
| `find_city("Japan", "Tokyo")` | resolve a display name, case-insensitive |
| `airports_for("Japan", "Tokyo")` | `("NRT", "HND")`, or `()` |
| `primary_city("Japan")` | the assumed city when only a country is given |
| `city_options()` | `{country: [{slug, name, label}]}`, JSON-ready for a form |

Nothing raises. An unresolvable lookup returns `None` or an empty tuple, which
callers report as "we cannot route this" rather than catching an exception.

## What changed in the request contract

`TravelRequest` (`flaskapp/travel_ai/schemas.py`) gained two **optional** fields.
Country stays the field of record, so nothing that already read `origin` /
`destination` needs to change:

```python
origin: str                     # country — unchanged, still required
destination: str                # country — unchanged, still required
origin_city: str | None         # NEW, display name e.g. "Singapore"
destination_city: str | None    # NEW, display name e.g. "Tokyo"
```

`travel_requests` gained matching **nullable** columns `origin_city` and
`destination_city`. NULL means the request was made at country granularity —
normal for rows written before this change, and still valid for API callers who
send no city.

## Multi-airport cities matter to you

A city can have several airports and Flight Agent now searches all of them, so
**the arrival airport is not predictable from the destination city**. A Tokyo
trip may land at NRT or HND, and the transfer time to the same hotel differs by
about an hour between them.

Read the arrival airport from the selected flight candidate
(`FlightCandidate.dest_airport`), not from the city. `TripContext` carries both
shapes:

```python
ctx.dest_city        # "Tokyo"
ctx.dest_airports    # ["NRT", "HND"]  <- what was searched
ctx.dest_airport     # "NRT"           <- primary only, for display
```

If you need to filter on route, use the **list**. The scalar is a display
convenience and using it for matching reintroduces the single-gateway bug that
city intake exists to remove.

## Seed inventory

`seed_data.py` derives its own coverage from its rows, so it stays honest as the
dataset grows:

```python
from flaskapp.travel_ai.agents.flight_agent.seed_data import (
    SEED_AIRPORTS, SEED_ROUTES, covers_route,
)
covers_route(["SIN"], ["NRT", "HND"])   # True if ANY pair is stocked
```

Flight inventory is SIN-origin hub-and-spoke: **280 generated rows** across
SIN ↔ NRT, HND, LHR, LGW, SYD, BKK, DMK, HKG, KIX, MEL, ICN, KUL, DPS, CGK,
HKT, TPE, DXB, CDG — plus 4 hand-written golden-scenario flights. If you seed
hotels for the same cities, a demo runs end to end without gaps.

Regenerate with `python scripts/generate_flight_seed_csv.py`. It is
deterministic, and city routes use a separate RNG stream so regenerating never
rewrites the original 100 rows.

## Two things to know before you extend the dataset

- **Hong Kong is a city of China** (`cn-hong-kong`), by team decision.
  `countries.py` has no separate Hong Kong entry, so this is what makes its
  existing `HKG` flight inventory selectable at all. Pick "China" then
  "Hong Kong" in the form.
- **`tests/test_places.py` guards the data.** Country names must match
  `countries.py` exactly, no airport may serve two cities, city names must be
  unique per country and alphabetical within it. A bad row fails the build
  instead of silently hiding a city from the form.
