# Handoff: Hotel & Transport Agent

**For:** the developer building the Hotel & Transport Agent
**From:** the Flight Agent work
**Last updated:** 2026-08-11

Flight Agent is built and working. This document tells you what it produces,
what you can rely on, and where to plug your work in.

Read this first, then [`docs/places_contract.md`](../places_contract.md) for the
exact API of the shared city dataset.

---

## 1. The one-minute version

- Trips are now booked **country + city**, not country alone.
- `flaskapp/places.py` is the shared list of cities — **254 cities, 61
  countries**. Use it. Don't build a second city list.
- **Key your hotel tables on `city.slug`** (`jp-tokyo`), never on the name.
- **A city can have several airports.** Read the arrival airport off the chosen
  *flight*, not off the city, or your transfer times will be wrong.
- Flights come from a static dataset of 284 rows by default. 16 cities have real
  flight data — seed your hotels for those and the demo runs end to end.

---

## 2. What your agent receives

Your agent is a LangGraph node like the others. It is given the whole travel
request plus whatever other specialists have already produced.

The request now carries four location fields:

```python
request.origin             # "Singapore"  — country, always present
request.destination        # "Japan"      — country, always present
request.origin_city        # "Singapore"  — city, may be None
request.destination_city   # "Tokyo"      — city, may be None
```

**Why country and city both exist.** Country stays the field of record because
Risk & Advisory reasons at country level (visas, travel advisories) and the
database column is `NOT NULL`. The city is additive.

**Why city can be `None`.** Requests made through the API without a city, and
any request stored before this feature existed, have no city. Handle it:

```python
city = request.destination_city or request.destination   # fall back to country
```

---

## 3. The city dataset — build your inventory on this

```python
from flaskapp.places import cities_for, city_by_slug, find_city

tokyo = city_by_slug("jp-tokyo")
tokyo.slug             # "jp-tokyo"      <- store THIS as your foreign key
tokyo.name             # "Tokyo"         <- display only
tokyo.country          # "Japan"
tokyo.airports         # ("NRT", "HND")
tokyo.primary_airport  # "NRT"
```

### Use the slug, not the name

Your hotels table should look like this:

```sql
CREATE TABLE hotels (
    id            INTEGER PRIMARY KEY,
    city_slug     TEXT NOT NULL,   -- "jp-tokyo"  <- correct
    name          TEXT NOT NULL,
    ...
);
```

Not this:

```sql
    city_name     TEXT NOT NULL,   -- "Tokyo"     <- will break
```

Display names get corrected over time — a diacritic fixed, "Osaka" clarified to
"Osaka (Kansai)". If your rows point at the display text, every one of them
breaks on that edit. The slug never changes. This is the ordinary rule of
joining on an ID rather than a label.

### Functions you'll use

| Call | Returns |
|---|---|
| `cities_for("Japan")` | every city in a country, alphabetical |
| `city_by_slug("jp-tokyo")` | one city, or `None` |
| `find_city("Japan", "Tokyo")` | resolve a display name, case-insensitive |
| `airports_for("Japan", "Tokyo")` | `("NRT", "HND")`, or `()` |
| `city_options()` | `{country: [{slug, name, label}]}` — ready for a form |

Nothing raises. An unknown city returns `None` or an empty tuple. The house style
is that "we can't do this" is an answer you show the traveller, not an exception
you throw.

---

## 4. The trap: airports are not predictable from the city

This is the one thing most likely to cause a real bug in your agent.

24 of the 254 cities have more than one airport, and Flight Agent searches all
of them. **A Tokyo trip may land at Narita or Haneda.** They are roughly an hour
apart in transfer time to central Tokyo.

So do this:

```python
# CORRECT — the airport of the flight that was actually selected
arrival_airport = flight_candidate.dest_airport      # "HND"
```

Not this:

```python
# WRONG for transfer calculations — this is just the city's default label
arrival_airport = trip_context.dest_airport          # always "NRT" for Tokyo
```

`TripContext` gives you all three shapes, and they mean different things:

| Field | Example | Meaning |
|---|---|---|
| `dest_city` | `"Tokyo"` | the city the traveller chose |
| `dest_airports` | `["NRT", "HND"]` | every airport that was searched |
| `dest_airport` | `"NRT"` | primary only — **display label, not a fact about the trip** |

Multi-airport cities you'll hit in the demo data: Tokyo (NRT/HND), London
(LHR/LGW/STN/LTN), Bangkok (BKK/DMK), Osaka (KIX/ITM), Seoul (ICN/GMP).

---

## 5. Where flight data actually comes from

Two sources behind one interface. **Seed is the default and is what you should
develop against.**

| | `seed` (default) | `duffel` |
|---|---|---|
| Data | 284 static rows | live supplier search |
| Turned on by | nothing — it's the default | `FLIGHT_INVENTORY_SOURCE=duffel` + a token |
| Prices | fixed, repeatable | change every call |
| Cost | free | billed per search |

Duffel is fully wired but **switched off**. You do not need a token, and you
should not develop against live data — prices move, so nothing is reproducible.

### Cities with real flight inventory

All routes are Singapore-origin. These 16 cities have flights, so seed your
hotels here and a demo runs end to end with no gaps:

**Singapore, Tokyo, Osaka, London, Sydney, Melbourne, Bangkok, Hong Kong,
Seoul, Kuala Lumpur, Bali, Jakarta, Phuket, Taipei, Dubai, Paris**

Note that flights only exist on **specific dates** between 2026-08-24 and
2026-10-08. Picking a city with no flight on the chosen date correctly returns
no candidates — that's the dataset being small, not a bug.

To see what exists:

```python
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY
for f in SEED_FLIGHT_INVENTORY[:5]:
    print(f.origin_airport, f.dest_airport, f.dep_ts, f.price)
```

Hong Kong is listed as a city of China (`cn-hong-kong`) — `countries.py` has no
separate entry for it, so that is what makes its flights selectable.

---

## 6. Your prompt

`flaskapp/travel_ai/agents/hotel_transport_agent/prompt.py` is yours. I added two
sentences telling it to anchor on `destination_city` and to note which airport
the flight actually arrives at. **Reword or remove it freely** — I edited a file
whose header says you own it, so treat my wording as a placeholder rather than
something to preserve.

---

## 7. House rules worth following

These aren't mine — they're the conventions the rest of the codebase already
follows, and matching them will make review easier.

1. **Never invent availability, prices, or accessibility.** If the data doesn't
   say, the answer is "unknown", not a guess. Flight Agent models unknown
   accessibility as a genuine third state, not as `False`.
2. **Don't raise for a data problem.** No hotels for a city is a result with a
   written reason, not an exception. The orchestrator can negotiate around a
   result; it can't negotiate around a stack trace.
3. **Say what you assumed.** If you substitute something — a nearby city, a
   default check-in time — put it in the warnings so the traveller sees it.
4. **Accessibility needs are hard constraints**, not preferences. They filter,
   they don't merely re-rank.

---

## 8. Checklist to get started

1. Read [`docs/places_contract.md`](../places_contract.md) — the dataset API.
2. Look at `flaskapp/travel_ai/agents/flight_agent/providers/` — the provider
   pattern (`covers()` / `fetch()`) is worth copying for hotel inventory.
3. Create your hotel seed data keyed on `city_slug`, covering the 16 cities above.
4. Wire your node the way `flight_agent/agent.py` does.
5. Run `python -m pytest tests/ -q` — 261 tests should pass before you start, so
   you can tell your breakages from pre-existing ones.

## Questions this document doesn't answer

- **How hotels get priced or ranked** — your design decision.
- **Whether hotel inventory should live in SQLite or a static file** — Flight
  Agent uses a static CSV plus a provider interface; copy that if it suits.
- **How transfer times are calculated** — not modelled anywhere yet.
