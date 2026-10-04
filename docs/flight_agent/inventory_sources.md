# Flight inventory source

Flight Agent reads inventory through an `InventoryProvider`
(`flaskapp/travel_ai/agents/flight_agent/providers/`) rather than importing a
dataset. One provider exists: `seed`.

| | `seed` |
|---|---|
| Data | 1568 static rows: 4 golden + 1564 generated CSV |
| Selected by | nothing — it is the only source; an unknown `FLIGHT_INVENTORY_SOURCE` falls back to it with a visible warning |
| Determinism | Total. Golden scenarios and the bias audit are pinned to it |
| Accessibility fields | Real `True`/`False` on every row |
| Seat inventory | Modelled on every generated CSV row |

The golden scenarios, `test_flight_golden.py` and `test_flight_bias_audit.py`
assert exact ranking output, which only a static dataset can provide.

`InventoryProvider` stays a Protocol so a live supplier can be added later
without touching the node. Anyone adding one should re-size
`FLIGHT_AGENT_MAX_PROVIDER_CALLS` first: it bounds `fetch` calls, a live
`fetch` may fan out into several billed searches, and since #57 the empty-leg
date walk can need up to 12 searches on a full walk of both legs.

## Multi-airport cities

Intake collects a city, and a city can have several airports (Tokyo → NRT/HND,
London → LHR/LGW/STN/LTN). The provider searches all of them and `domain.py`
ranks the merged set, so the cheapest option wins regardless of which airport it
departs from — the whole reason city granularity was worth collecting. Seed
filters one in-memory list, so extra airports cost nothing.

## Unverified accessibility is not "unavailable"

`FlightInventoryItem.wheelchair_assist_available` and `.step_free_boarding` are
`bool | None`. `None` means *the source does not publish this*. Seed always
states a real value, but a live supplier feed typically has no such field, so
the case is handled now rather than discovered later:

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
