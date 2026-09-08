# Flight inventory sources

Flight Agent reads inventory through an `InventoryProvider`
(`flaskapp/travel_ai/agents/flight_agent/providers/`) rather than importing a
dataset. Two providers exist.

| | `seed` (default) | `duffel` |
|---|---|---|
| Data | 1568 static rows: 4 golden + 1564 generated CSV | Live supplier search |
| Selected by | nothing — it is the default | `FLIGHT_INVENTORY_SOURCE=duffel` + `DUFFEL_API_TOKEN` |
| Determinism | Total. Golden scenarios and the bias audit are pinned to it | None — results change between calls |
| Accessibility fields | Real `True`/`False` on every row | **Not published — always `None`** |
| Seat inventory | Modelled on every generated CSV row | **Not available** |

Seed remains the default deliberately. The golden scenarios, `test_flight_golden.py`
and `test_flight_bias_audit.py` assert exact ranking output, which a live feed
cannot provide, so switching sources is an explicit opt-in rather than something
a stray credential in the environment can trigger.

## Enabling Duffel

1. Create an account at [duffel.com](https://duffel.com) and open the dashboard
   in **Developer / test mode**.
2. Copy the test access token (it starts `duffel_test_`) into `.env.secrets`
   as `DUFFEL_API_TOKEN=`. That file is gitignored; never put the token in
   `.env.example` or in source.
3. Set `FLIGHT_INVENTORY_SOURCE=duffel` in `.env`.
4. Verify with `python scripts/duffel_smoke.py`, which runs one search and
   prints the mapped rows without involving an LLM or the graph.

Setting `FLIGHT_INVENTORY_SOURCE=duffel` **without** a token falls back to seed
and attaches a warning to the finding saying so — a missing credential degrades
the data source rather than taking the planner down, but never silently.

## Why there is no Duffel SDK dependency

Duffel's official Python client (`duffelhq/duffel-api-python`, PyPI `duffel-api`)
was **archived by Duffel on 2024-09-12** and is read-only; they discontinued it
for lack of adoption. Depending on an abandoned package for a credentialed
network path is worse than calling one REST endpoint directly, which is what
`providers/duffel.py` does with `requests`.

The `@duffel/api` package that appears in most Duffel examples is the
Node/TypeScript SDK and is not usable from this codebase at all.

## Multi-airport cities

Intake collects a city, and a city can have several airports (Tokyo → NRT/HND,
London → LHR/LGW/STN/LTN). Both providers search all of them and `domain.py`
ranks the merged set, so the cheapest option wins regardless of which airport it
departs from — the whole reason city granularity was worth collecting.

The two providers pay for this differently:

- **Seed** filters one in-memory list, so extra airports cost nothing.
- **Duffel** needs one search per airport *pair*, so London → Tokyo would be
  eight billed, rate-limited calls. Each end is capped at its two main gateways
  (`MAX_AIRPORTS_PER_CITY`), and when the cap bites, the finding says so rather
  than quietly searching less than the traveller asked for. A pair that fails
  does not fail the fetch; its note is collected and the other pairs still
  contribute.

## Known limitations of the Duffel path

These are properties of the API, not bugs in the mapping.

- **No accessibility data.** Duffel's offer schema has no wheelchair-assistance
  or step-free-boarding field. Every Duffel row therefore carries `None`, which
  the pipeline treats as a third state — see below.
- **No seat counts.** Duffel does not publish remaining seats. Rows are given
  `seats_available = party size` so the seat filter cannot exclude every live
  option on data we do not have, and the finding says so.
- **No seat-map aggregates.** `SeatInventory` (window/aisle/adjacent-block
  counts and fees) would need a separate Seat Maps call *per offer*. Not
  implemented; Duffel rows carry `seat_inventory=None`, which the existing
  backward-compatible `None` handling in `domain.py` already skips.
- **Apportioned prices.** Duffel prices a return trip as one offer containing
  two slices. This agent models legs independently, so the total is divided by
  passengers and legs. A real one-way fare is not half a round trip; the
  Duffel assumption string on every option states this.
- **Unstable `flight_id`.** A seed ID identifies a flight. A Duffel ID
  (`off_…:0`) identifies a *quote*, and offers expire (`offer.expires_at`), so
  it will not resolve in a later session.
- **Sandbox data is not realistic.** `duffel_test_` tokens resolve to Duffel
  Airways (IATA `ZZ`), which Duffel documents as returning deliberately
  unrealistic schedules and prices. Real airlines' sandboxes are external to
  Duffel and are often empty or down. Use **LHR→JFK, one adult** for smoke
  testing — the search Duffel documents as reliably served. The golden
  SIN→NRT scenario will not return usable sandbox data, and that is expected.
- **Timezones need reassembly.** Duffel sends local-but-naive timestamps with
  the IANA zone on the place object. `providers/duffel.localise` recombines
  them, because `FlightInventoryItem` requires a UTC offset and
  `domain._is_red_eye` plus check-in feasibility both read it. If Duffel ever
  drops `time_zone`, timestamps degrade to naive (recoverable) rather than
  taking a guessed offset (silently wrong); `scripts/duffel_smoke.py` warns
  when that happens.

## Unverified accessibility is not "unavailable"

`FlightInventoryItem.wheelchair_assist_available` and `.step_free_boarding` are
`bool | None`. `None` means *the source does not publish this*, and it is
handled as its own case throughout:

| Situation | Behaviour |
|---|---|
| Implicit filter (traveller stated a wheelchair need) | **Not excluded.** Excluding on unknown would return zero options to every wheelchair user the moment inventory goes live — which reads as "no flights exist for you" rather than "we could not check". |
| Ranking | Verified-accessible flights rank **above** unverified ones for travellers who stated the need. Inert for everyone else, and inert for seed data, so no existing ranking moves. |
| Explicit `constraints.require_wheelchair_assist` | **Fail-closed.** That constraint is orchestrator-issued mid-negotiation, usually after Accessibility Agent vetoed a round, so "not published" does not satisfy it. |
| Traveller-facing `Option` | Gets its own wording — "could not be verified … confirm directly with the carrier" — never the "is not offered" message, and never silence. |

Defaulting `None` to `True` would invent an accessibility guarantee; defaulting
it to `False` would hide every live flight from the traveller who needs one.
Both failures land on the same person. `tests/test_flight_accessibility_unknown.py`
holds all four rows of that table.
