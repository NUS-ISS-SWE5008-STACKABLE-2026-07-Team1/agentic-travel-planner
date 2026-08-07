# Flight Agent — Prompt Spec (Stage 1: Develop Use Case)

Per `localfolder/llmops_plan.md` §8 stage 1 — define the input/output contract
and example cases *before* writing the prompt. These examples become the
first `promptfoo` test cases once prompt engineering starts.

## Task

Given a `flight.proposal.request` (trip context + optional renegotiation
constraints), return a `flight.proposal`: ranked, feasible flight candidates
for the outbound and return legs, or an empty candidate list per leg if
nothing is feasible — never a fabricated flight.

Note: candidate search/filter/rank against inventory is **deterministic**
(`flaskapp/travel_ai/agents/flight_agent/domain.py`) — no LLM call needed for that part.
Where an LLM is likely needed is generating the natural-language explanation
of *why* a candidate was chosen/rejected (selection factors, tradeoffs) for
the explainability narrative — that prompt is not yet written; this spec
covers the underlying data contract it will summarize.

## Input contract

`FlightProposalRequest` (`flaskapp/travel_ai/agents/flight_agent/schemas.py`):
- `trip_context`: origin/dest airports, dest city/country, dates, party,
  budget_total, accessibility_needs, passport_country, preferences
- `constraints` (present only on renegotiation): `arrive_before`,
  `require_wheelchair_assist`, `max_price`

## Output contract

`FlightProposal`: list of `FlightCandidate` — flight_id, direction
(OUTBOUND/RETURN), dep_ts, arr_ts (destination-local), dest_airport, stops,
price, seats, wheelchair_assist_available, step_free_boarding.

## Example cases (golden scenario #1 — `db_schema.md` §7)

1. **Round 0, no constraints, wheelchair need in trip context** — SIN→NRT
   2026-09-01, return 2026-09-05, accessibility_needs=["wheelchair"].
   Expect both SQ636 (cheap, arr 23:40) and SQ632 (mid, arr 15:10) as
   outbound candidates, cheapest first; both return options too.
2. **Renegotiation: arrive_before 23:00** (Hotel/Transport flagged the
   23:40 arrival as infeasible against last train service) — expect SQ636
   dropped, SQ632 the only outbound candidate.
3. **Renegotiation: max_price 400** (Budget Validator flagged the
   accessible-taxi + 24h hotel combo as exceeding cap) — expect only the
   flights at or below SGD 400 per leg.
4. **Inaccessible-but-cheaper flight present** — a flight with
   `wheelchair_assist_available=False` priced below the accessible options
   must never appear when `accessibility_needs` includes "wheelchair" and no
   explicit constraints override it.
5. **No feasible option for the requested date** — return an empty
   candidate list for that leg, not an error and not a fabricated flight.

Cases 1–5 are implemented as unit tests in `tests/test_flight_domain.py`
against the deterministic domain logic; once the explanation-generation
prompt is written, the same cases should be re-used as its `promptfoo` set.
