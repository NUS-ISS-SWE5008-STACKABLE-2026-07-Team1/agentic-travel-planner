# Tier packages and airline-style flight cards

Status: designed
Date: 2026-09-14

## Problem

Price tiers landed section-major: a Flight section with its own Budget / Comfort
/ Luxury columns, then a Hotel section with its own. A traveller comparing
options reads two grids and has to pair them mentally — the cheapest flight sits
in one grid and the cheapest hotel in another, and nothing puts them side by
side as a trip.

Flight options also read as prose. A grounded flight carries everything an
airline site shows:

```
name: JL6006-20261010 (Outbound)
desc: Outbound to HND, departing 2026-10-10T17:15+08:00, arriving 2026-10-11T01:06+09:00.
```

Flight number, date, both local times — buried in a sentence, so a traveller
scanning six options reads six sentences instead of six rows.

## Scope

In scope: transposing the banded sections into one tier-major grid, and giving
grounded flights an airline-style card.

Out of scope, explicitly:

- **Tiering anything else.** Accessibility, risk and transport stay as they are.
- **Pairing a specific flight with a specific hotel.** A Budget column shows the
  cheapest flights and the cheapest hotels; it does not claim they are a
  costed bundle, and no total is shown across them.
- **Giving fallback flights a schedule.** The prompt-only path has no flight
  number and no timestamps to give.

## Decisions

| Decision | Choice | Rejected alternative |
| --- | --- | --- |
| Grid shape | One tier-major grid; flights and hotels inside each column | Two section-major grids |
| Where banded sections live | `packages`; removed from `sections` | Present in both |
| Banding across sections | Independent per section | One shared ranking |
| Flight schedule | An optional `schedule` on `Option` | Parsing the description text |
| Times | Local to each airport, as the ISO offsets give them | Converted to one zone |
| Formatting | Server-side | In the renderer |

Four earn an explanation.

**Banding stays independent per section.** Six flights split 2/2/2 while five
hotels split 2/2/1, so a column shows whatever each contributed rather than a
forced pairing. Ranking them together would be meaningless — a flight and a
hotel are not comparable on price, and "the third cheapest thing" is not a tier.

**A banded section appears in `packages` and nowhere else.** Leaving it in
`sections` too would put the same options on screen twice and give two places
to fix when one is wrong.

**Times are local to each airport.** The ISO strings carry different offsets —
`+08:00` departing, `+09:00` arriving — because they are wall-clock times at two
different places. Converting both to one zone would display times that no
boarding pass agrees with. The formatter reads the wall clock as given and never
converts.

**Formatting is server-side.** `10 Oct 2026`, `17:15`, and the `+1` next-day
marker are computed where a test can assert them. The next-day case is the one
that matters: a flight departing 17:15 and arriving 01:06 is not a nineteen-hour
mistake, and a renderer that silently drops the day change says it is.

## Flow

```
findings
   │
   ├─ plan_sections(findings)      unbanded sections only now:
   │                               transport, accessibility, risk
   │
   └─ plan_packages(findings)      tier-major transpose
          │
          │   for each tier label present in ANY banded section:
          │       Budget ─┬─ ✈️ Flight details  → that section's Budget options
          │               └─ 🏨 Hotel details   → that section's Budget options
          │
          │   a section that contributed nothing to a tier is simply absent
          │   from that column — which is also how a hotel-only plan renders,
          │   with no flight block and no special case
          ▼
PlanResponse.packages
```

## Components

### `flaskapp/travel_ai/schemas.py`

```python
class OptionSchedule(BaseModel):
    reference: str     # JL6006
    depart: str        # 2026-10-10T17:15+08:00
    arrive: str        # 2026-10-11T01:06+09:00
    dest_code: str     # HND
    stops: int = 0

class Option(BaseModel):
    ...
    schedule: OptionSchedule | None = None
```

Optional and defaulted, so every stored row, existing test and A2A artifact
stays valid. Only the flight builder fills it.

```python
class PlanGroup(BaseModel):     # one section's contribution to one tier
    title: str
    icon: str
    options: list[Option]

class PlanPackage(BaseModel):   # one column
    label: str                  # Budget | Comfort | Luxury | Not priced
    groups: list[PlanGroup]
```

### `flaskapp/travel_ai/agents/flight_agent/agent.py`

`_candidate_to_option` fills `schedule` from the `FlightCandidate` it already
holds: `flight_id`, `dep_ts`, `arr_ts`, `dest_airport`, `stops`. No new data is
fetched — the values were already being written into the description sentence.

### `flaskapp/travel_ai/sections.py`

```python
def format_schedule(schedule: OptionSchedule) -> dict[str, str]
def plan_packages(findings: list[AgentFinding]) -> list[PlanPackage]
```

`format_schedule` returns `{"reference", "date", "depart", "arrive",
"day_offset"}` — `10 Oct 2026`, `17:15`, `01:06`, and `"+N"` where N is the
number of local calendar days between departure and arrival — `"+1"` for the
common overnight case, `""` when they land on the same date. Computed rather
than assumed to be at most one: a two-stop itinerary can cross two dates. Pure string
work on the ISO values; no timezone conversion.

`plan_packages` walks `TIER_LABELS + (UNPRICED_LABEL,)` in order and, for each,
collects the matching tier from every banded section. A tier with no groups is
dropped, so a plan whose flights are all unpriced does not render three empty
columns.

`plan_sections` keeps its signature and drops `BANDED_SECTIONS` from its output.

This leaves `PlanSection.tiers`, added yesterday, holding a single unlabelled
tier for every section it now returns — the banded ones have moved out. It stays
rather than being removed: it is defaulted, the A2A artifact and existing tests
read it, and removing a field from a published contract to tidy up is a worse
trade than one redundant accessor. The renderer's "grid when a tier is labelled"
rule consequently never fires for a section; grids come from `packages` now.

### `flaskapp/static/js/app.js` and `app.css`

One `.plan-tier-grid` of columns. Each column renders its label as a heading,
then each group as an icon plus title followed by its cards.

Tier headings become bold, larger and letter-spaced, with a rule beneath. The
three tints are declared once as CSS custom properties (`--tier-budget`,
`--tier-comfort`, `--tier-luxury`) rather than inlined, so the palette lives in
one place and a reader can see the three are a set.

A card whose option has a `schedule` renders airline-style — reference and date
on one line, `17:15 → 01:06` with a superscript `+1` on the next — above the
existing description and price. A card without one renders exactly as today.

## Error handling

| Condition | Behaviour |
| --- | --- |
| A section contributes nothing to a tier | That group is absent from the column |
| A tier has no groups at all | The column is dropped |
| No banded sections ran | `packages` is empty; only `sections` renders |
| Option has no `schedule` | The card renders as it does today |
| `depart`/`arrive` unparseable | The schedule block is skipped; the card still renders |

## Known limitations

- **A column is not a costed bundle.** It shows the cheapest flights beside the
  cheapest hotels, with no combined total; a traveller could pick one of each
  and exceed their budget.
- **Fallback flights have no card.** Destinations outside the seed inventory
  produce prose options, which is most flight options today.
- **Tier labels remain relative** to what the search returned, as before.
- **Frontend rendering stays unverified** for want of a JS harness.

## Testing

- The transpose: independent banding preserved, a section missing from a tier,
  a tier with no groups dropped, hotel-only producing hotel-only columns.
- Banded sections no longer appear in `plan_sections` output.
- `format_schedule`: date as `10 Oct 2026`, 24-hour times, `+1` on the
  next-day case, empty offset on a same-day flight, and an unparseable value
  handled rather than raised.
- `_candidate_to_option` fills `schedule`; the fallback builder leaves it None.
- `Option` without `schedule` still validates, so stored rows stay readable.
