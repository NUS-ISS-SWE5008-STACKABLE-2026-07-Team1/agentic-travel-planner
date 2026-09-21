# Budget-fitting package, and the transport that was never fetched

Status: designed
Date: 2026-09-17

## Problem

Two problems, and the second is why the first is possible.

**Transport has never appeared in a plan.** Every grounded run records
`transport_option_count: 0`, while the seed holds 23 city/airport pairs and 46
options — including `('jp-tokyo', 'HND')` and `('jp-tokyo', 'NRT')`. The lookup
has never reached the data.

`to_hotel_request` derives `arrival_airport` from the flight agent's options in
`state["findings"]`. Both agents start from `START` and run concurrently, so
when the hotel node executes those findings are empty. `arrival_airport` is
always None, the `if arrival_airport and ctx.dest_city_slug` guard never opens,
and the agent then reports "No verified transport options are available for the
selected arrival airport" — which reads like a data gap and is a wiring bug.

**A traveller states a total budget and nothing spends it.** The plan lists
flights and hotels in price tiers, but nobody adds a flight to a hotel to a
transfer and says "this comes to 4,180 of your 5,000".

## Scope

In scope: fetching transport for every airport the destination city has, and
recommending one combination that fits the stated budget.

Out of scope, explicitly:

- **Booking, holding or pricing live.** The recommendation is an arithmetic
  proposal over options the specialists already returned.
- **Staging the graph.** Considered, and unnecessary — see "Decisions".
- **Multi-city transport.** Transport is fetched for the destination city only.

## Decisions

| Decision | Choice | Rejected alternative |
| --- | --- | --- |
| Where the airport comes from | The destination city's own airports, via `places.py` | The flight agent's findings |
| Graph topology | Unchanged; still a parallel fan-out | Stage flight → hotel |
| Pairing transport to a flight | A shared `Option.airport` | Matching on prose in the description |
| What is recommended | One combination, highest total not exceeding budget | One per tier; closest either side |
| Search | Exhaustive over the returned options | A greedy or heuristic pick |
| Nothing fits | No recommendation, and the shortfall stated | The cheapest combination anyway |
| Currency mismatch | Excluded from the search | Summed as raw numbers |

Four earn an explanation.

**The dependency was avoidable, not inherent.** The obvious fix is to edge
`START → flight_agent → hotel_transport_agent` so the hotel agent sees real
flights. That costs latency — the two slowest agents run in series — and does
not even settle the question, because one finding spans several arrival
airports: a recent run returned NRT twice and HND once. Taking the first
outbound candidate, as the current code does, would produce a plan that says
fly to Haneda and take the Narita Express. Resolving the city's airports from
`places.py` needs no findings at all, so the fan-out stays parallel and every
airport the traveller might land at is covered.

**`Option.airport` is the key that makes a package coherent.** A flight knows
where it lands and a transfer knows which airport it serves; without a shared
field the two can only be paired by parsing prose. One additive, defaulted
string on the shared contract is the smallest thing that makes the pairing a
lookup rather than a guess.

**The search is exhaustive because it can afford to be.** A finding holds at
most six flights and five hotels, so the combination space is small enough to
enumerate. A greedy pick — cheapest flight, then cheapest hotel — can miss the
better answer: a dearer flight paired with a much cheaper hotel may land closer
to budget. Enumerating is both simpler to reason about and deterministic, which
a heuristic tuned by feel would not be.

**Nothing fitting is an answer.** A planner that never books must not propose a
trip the traveller cannot afford. When the cheapest possible combination still
exceeds the budget, the recommendation is withheld and the shortfall named, so
the traveller learns what the trip actually costs rather than being shown a
package they would have to abandon at checkout.

## Flow

```
TravelRequest.budget, .currency
findings
   │
   ├─ hotel_transport_agent
   │     city = find_city(destination, destination_city)      places.py
   │     for airport in city.airports:                        ('NRT', 'HND')
   │         transport_for(city.slug, airport)                seed lookup
   │         → Option(category="transport", airport=airport)
   │
   └─ recommend_package(request, findings)
         outbound  = flights where schedule.direction == OUTBOUND
         inbound   = flights where schedule.direction == RETURN
         hotels    = hotel-category options
         transfers = transport-category options
         │
         │   for each (outbound, inbound, hotel, transfer):
         │       transfer must serve outbound.airport, or be absent
         │       total = sum of the parts
         │       keep the highest total that is <= budget
         │
         ├─ a combination fits ──► PlanRecommendation(items, total, remaining)
         └─ none fits          ──► no recommendation, plus the shortfall
```

## Components

### `flaskapp/travel_ai/schemas.py`

```python
class OptionSchedule(BaseModel):
    ...
    direction: str = ""          # OUTBOUND | RETURN

class Option(BaseModel):
    ...
    airport: str = ""            # the airport this option lands at or serves

class PlanRecommendation(BaseModel):
    items: list[Option]
    total: float
    currency: str
    remaining: float             # budget - total, never negative
    note: str = ""               # why there is no recommendation, when there is none
```

Both new `Option` fields are defaulted, so stored rows, golden scenarios and the
A2A artifact stay valid.

### `flaskapp/travel_ai/agents/hotel_transport_agent/agent.py`

Replaces the `ctx.arrival_airport` lookup. The city resolves through
`places.find_city(destination, destination_city)`, and `transport_for` is called
once per airport in `city.airports`. Each resulting `Option` carries
`category="transport"` and `airport=<that airport>`.

When the city resolves but no pair has seed transport, the existing note stands
— that is a real data gap rather than the wiring bug it currently reports.

### `flaskapp/travel_ai/agents/flight_agent/agent.py`

`_candidate_to_option` sets `airport=candidate.dest_airport` and
`schedule.direction=candidate.direction`. Both values are already in hand.

### `flaskapp/travel_ai/recommendation.py` (new)

```python
def recommend_package(request: TravelRequest, findings: list[AgentFinding]) -> PlanRecommendation
```

Pure — a request and findings in, a recommendation out. No model, no IO.

It ALWAYS returns a `PlanRecommendation`, never None: when nothing fits, `items`
is empty and `note` carries the reason. A None would leave the renderer with
nothing to say, and "this trip does not fit your budget, by 400 SGD" is the most
useful thing the feature can tell a traveller.

Only priced options in `request.currency` are considered. A leg with no
candidates (no return flights, say) means no recommendation rather than a
partial one: a package missing its return flight is not a trip.

Transport is optional in the sense that a city may have none; the combination is
then flight + flight + hotel, and the recommendation says so rather than
failing.

### `flaskapp/travel_ai/service.py`

Calls `recommend_package` beside `plan_packages` and puts the result on
`PlanResponse.recommendation`, which is therefore always present. Derived at
response time, never persisted — the
options it references are already stored.

### `flaskapp/static/js/app.js` and `app.css`

A card above the tier grid: the chosen flights, hotel and transfer, the total,
and what remains of the budget. Its accent is distinct from the three tier
colours, so "recommended" does not read as a fourth tier.

When there is no recommendation the card still renders, carrying the note, so
the traveller learns the trip does not fit rather than seeing nothing.

## Error handling

| Condition | Behaviour |
| --- | --- |
| No flight or no hotel options | No recommendation; note explains which is missing |
| No transport for the city | Recommendation without a transfer; note says so |
| Cheapest combination exceeds budget | No recommendation; note names the shortfall |
| Options in a currency other than the request's | Excluded from the search |
| `budget` absent or non-positive | No recommendation; nothing to fit |
| City does not resolve | No transport fetched; existing unresolved note stands |

## Known limitations

- **Hotel cost is whatever the specialist quoted.** If that is per night rather
  than per stay, the total inherits the error; the recommendation does not
  re-derive costs.
- **One combination only.** No second-best, and no per-tier recommendation.
- **Transport is chosen on price alone**, like every other leg — whichever
  matching transfer brings the total closest to budget, not the fastest or the
  most accessible.
- **Budget is compared, not enforced.** `assess_plan` still owns any warning
  about the plan as a whole.

## Testing

- `recommend_package`: a combination fitting exactly at budget, fitting under,
  nothing fitting (shortfall named), a missing return leg, a missing hotel
  section, an option in another currency excluded, and transport matched by
  airport rather than by price alone.
- The search prefers a dearer flight with a cheaper hotel when that lands closer
  to budget — the case a greedy pick would miss.
- Transport: a multi-airport city yields options for every airport, each tagged;
  a city with no seed transport yields none and keeps its note.
- `_candidate_to_option` sets `airport` and `direction`.
- An `Option` without either field still validates.
