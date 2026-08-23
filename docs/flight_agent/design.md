# Flight Agent — design notes

Written 2026-08-16. Updated 2026-08-23: §2's account of when Path 2 fires was
wrong, and §3's fabricated-options problem has since been fixed — both are
corrected below and the changes are marked. The guardrail refactor sketched in
§7 is still proposed, not built.

Sources: `flaskapp/travel_ai/agents/flight_agent/` (agent.py, reasoning.py,
domain.py, guardrails.py), `flaskapp/travel_ai/agents/base.py`,
`flaskapp/travel_ai/safeguards.py`, and `docs/progress.md` Part B.

---

## 1. The core architecture: the tool decides, the model narrates

The Flight Agent is deliberately **not** an autonomous tool-calling loop. The
split is:

```
domain.py          propose_flights() / screen_flights()      deterministic
   ↓                search, filter, rank — plain Python
reasoning.py       the LLM reasons OVER that output          probabilistic
   ↓                writes the rationale, judges escalation
```

`propose_flights` always runs first and is never bypassed. The model never picks
flights — it explains a ranking that ordinary code already produced. This is why
`selection_factors` on each option restates only what the deterministic ranking
actually used (`agent.py:118-128`): the traveller-visible reasons match the code
that produced the order, not a model's account of it.

The consequence that matters: **if the LLM call fails entirely, the answer is
still usable.** `_fallback_response` returns the ranked candidates with no
narrative (`reasoning.py:114-119`). The agent degrades to workflow-level
reliability rather than blocking.

---

## 2. The two paths

`flight_node` branches on whether deterministic inventory can cover the trip
(`agent.py:214-232`):

```
flight_node(state)
  │
  ├─ provider.covers(request) AND provider.fetch() returned rows
  │     └─► run_flight_agent(...)          PATH 1 — grounded
  │
  └─ otherwise
        └─► prompt_only_node(state)        PATH 2 — prompt-only
              + ESTIMATE_WARNING appended
```

### Path 1 — grounded

Real inventory rows drive everything. The model receives the already-built
proposal and writes a rationale over it. Guarded at four points:

| Gate | Where | On failure |
| --- | --- | --- |
| Input screening (injection, bias, toxicity) | `reasoning.py:211`, **before** any model call | `_blocked_input_response`, model never invoked |
| Grounding — every cited `flight_id` must exist in the proposal | `reasoning.py:167` | retry, then fallback |
| Output screening (bias, toxicity) on the rationale | `reasoning.py:168` | retry, then fallback |
| Structured output (`FlightAgentResponse`) | `reasoning.py:154` | retry, then fallback |

### Path 2 — prompt-only

Delegates to `prompt_only_node`, which is a bare
`make_specialist_node(NAME, INSTRUCTION, llm, tracer)` (`agent.py:187`) with
`preflight` and `postprocess` left `None`.

**No input screening. No output screening. No grounding check** — there is
nothing to ground against. The only protections are `SYSTEM_POLICY`, the
untrusted-data labelling in `base.py:61-73`, and the pydantic schema.

### When Path 2 fires

Whenever `provider.covers()` is false or the fetch returns nothing:

- an airport pair the inventory doesn't stock,
- an unroutable country or city,
- a live Duffel search that failed, timed out, or came back empty.

**Correction (2026-08-23): dates do NOT send a request down Path 2 on the seed
provider.** `SeedInventoryProvider.covers()` delegates to `seed_data.covers_route()`,
which tests `(origin, dest) in SEED_ROUTES` and never looks at a date; `fetch()`
then returns all 284 rows unconditionally, and date filtering happens later in
`domain._screen_item`. So a stocked route with an unstocked date takes **Path 1
with an empty candidate list**, which `_coverage_warnings` labels honestly — not
Path 2, and with nothing fabricated. Path 2 on seed fires if and only if the
airport pair is unstocked, which (since every seed row has SIN at one end) means
any trip that does not touch Singapore.

Per `docs/progress.md` §6 the seed dataset is **284 rows, SIN-origin only, dates
2026-08-24 → 2026-10-08**. On the default `FLIGHT_INVENTORY_SOURCE=seed`, any
trip from another origin or outside that window takes Path 2. This is not a rare
edge case — for a demo driven from anywhere other than Singapore, it is the
*normal* path.

---

## 3. Does Path 2 mean the system hallucinates flights?

**Substantially, yes — and this is the design's weakest point.**

On Path 2 the model is asked to produce flight options with no inventory behind
it. Carrier names, flight numbers, departure times and prices in that output are
model-generated. Nothing in the codebase can verify them, because there is
nothing to verify them against.

The sharp version of the problem: `validate_grounded_explanation` — the guardrail
whose entire job is catching invented flight IDs — **only runs on Path 1**. On
Path 2 it is not merely bypassed; it is inapplicable, because the set it checks
membership against (`proposal.candidates`) does not exist. The one control aimed
at fabricated flights is absent from the one path where fabrication is possible.

### What the design does about it

The answer is **disclosure, not prevention**, and it is implemented carefully:

1. `ESTIMATE_WARNING` is appended to every finding from this path
   (`agent.py:231`): *"These flight options are model estimates, not drawn from
   verified inventory. Confirm every detail with the carrier."*
2. That string contains the marker `"model estimates"`, which
   `safeguards.UNVERIFIED_OPTIONS_MARKER` watches for.
3. `enforce_provenance_disclosure` (`safeguards.py:83`) then **prepends** a
   disclosure to `plan.limitations` naming the offending agent.
4. `agent_fallback` is written to the audit trace with
   `reason: "no inventory for route/dates"`, so the path taken is recoverable
   after the fact.

Step 3 exists because of an observed failure, and the reasoning behind it is
worth keeping: in end-to-end testing the orchestrator was seen *paraphrasing* the
agent's warning into a milder sentence near the bottom of the plan — semantically
close, but no longer a signal any code could rely on. Rather than test whether
the model happened to preserve it, the disclosure is now appended by the system.
Disclosure is a property of the pipeline, not of a given generation.

So the honest summary is: **the system generates unverified flight options and
guarantees they are labelled as unverified.** It does not prevent them.

### Whether that is acceptable

It is a genuine product trade-off, not an oversight:

- *For it:* "no options for this route" is useless to a traveller and gives the
  orchestrator nothing to negotiate around. A labelled estimate keeps the plan
  coherent and is arguably what a human agent would do off the top of their head.
- *Against it:* a fabricated flight number is more dangerous than no answer,
  because it looks actionable. A traveller who skims past a limitations block —
  and they do — is left holding a flight that never existed. The warning sits in
  `warnings`/`limitations`, while the invented option sits in `options`, which is
  the part the UI renders most prominently.

**This should be a recorded decision, not an emergent behaviour**, because it is
the first thing a reader will press on. Three defensible positions:

1. **Keep it, defend it** — document the trade-off explicitly, and strengthen the
   presentation so estimated options are visually distinct from grounded ones
   rather than distinguished only by a warning list.
2. **Degrade honestly** — return no options, with a clear "this route is outside
   our coverage" finding. Loses the demo, wins the argument.
3. **Narrow it** — allow prompt-only prose (route advice, typical price ranges,
   which carriers fly it) but forbid concrete `Option` entries with flight
   numbers and times. Keeps usefulness, removes the fabricated-artefact problem.

Option 3 is the one worth considering: nearly all the value of Path 2 is in the
guidance, and nearly all the risk is in the fabricated specifics.

### Decision (2026-08-23): option 3, adopted and enforced

`agent.py` now builds its fallback node with both hooks
`make_specialist_node` has always accepted and this call site never used:

```python
prompt_only_node = make_specialist_node(
    NAME, PATH2_INSTRUCTION, llm, tracer,
    preflight=make_preflight(NAME),
    postprocess=_forbid_concrete_options(NAME, tracer),
)
```

That single change closes both gaps at once — §6's empty "Path 2" column and this
section's fabricated options:

- **Input screening** now runs before the model is called, so blocked text never
  reaches it.
- **Output screening** runs on the generated prose, replacing a flagged finding
  wholesale.
- **`_forbid_concrete_options`** then empties `options` and appends
  `NO_CONCRETE_OPTIONS_WARNING`. `PATH2_INSTRUCTION` asks the model for
  route-level guidance and forbids specifics, but the prompt is only the intent
  — the postprocess is the guarantee.

`ESTIMATE_WARNING` is deliberately left untouched: it carries the substring
`safeguards.UNVERIFIED_OPTIONS_MARKER` matches on, and
`enforce_provenance_disclosure` depends on it. Stripping the options *and* the
warning would silently disable the plan-level disclosure — a trap
`tests/test_flight_path2_screening.py::test_path2_disclosure_survives_the_option_strip`
now holds shut.

Applied to `hotel_transport_agent` in the same change: the two modules are
near-duplicates, and `docs/progress.md` records that the last guardrail fix had
to be applied twice because one was missed. `tests/test_agent_parity.py` now
fails if either agent regains the gap alone.

Still open from the options above: option 1's presentational half — making
estimated output visually distinct in the UI, rather than distinguished only by
a warning list.

---

## 4. The relaxation fence ("option B")

When a leg comes back with no good options, the model may **propose** relaxing
one of the agent's own soft preferences. It never decides. Two fences, both in
code rather than in the prompt:

1. `PreferenceRelaxation.field` is a closed schema — it cannot express anything
   beyond the three soft preferences.
2. `domain.relaxation_is_valid()` re-confirms a real gap exists before anything
   is applied. The model's own claim is never trusted (`reasoning.py:232`).

Bounded, not a loop: **at most one extra tool call and one extra LLM pass, ever.**
A rejected relaxation is traced as `agent_relaxation_rejected` with
`"no matching gap found; ignored, not applied"`.

Worth noting for cost: the relaxation path calls `_reason_over_proposal` a second
time, and that function itself retries up to `MAX_ATTEMPTS = 2`. So a single
flight-agent run can invoke the model up to four times.

---

## 4b. The tool-calling loop (`FLIGHT_AGENT_MODE=agentic`)

Added 2026-08-23. **Off by default** — `structured` remains the shipped mode until
the latency measurement below is done.

`agentic.py` is a two-node subgraph *inside* the flight node: `plan` binds the
tools and proposes calls, `act` dispatches them, and a conditional edge ends the
loop when the model stops asking for tools. Not `tools_condition` on the top-level
graph, because `graph.py` is a fan-out/fan-in with a barrier edge and no
conditional edges — the specialists are peers, and one specialist's internals
should not become part of the workflow the others run through.

Three tools, wrapping `domain.py`: `search_flights`, `rank_flights`,
`relax_constraint`. `check_cost` and `check_accessibility` were considered and
dropped — Duffel publishes neither seat maps nor accessibility, and seed already
carries accessibility on every row, so both would have been tools with no data
behind them.

### What holds the loop, and none of it is the prompt

| Control | Where | What it prevents |
| --- | --- | --- |
| Argument envelope | `tools._validate_search_args` | A search wandering to a different trip: at most ±3 days from the **base** request (never the current one, or repeated shifts accumulate), and only airports already resolved for the traveller's cities |
| `LoopBudget` | `agents/loop.py` | Spiralling: turns, tool calls, provider calls, wall clock |
| Deterministic ranking | `domain._rank_key` | The model re-deriving an order in prose; it may permute named components, never invent one |
| Code-built options | `agent._candidate_to_option` | Fabricated flights — `Option`s come from `proposal.candidates`, which `propose_flights` builds from provider rows |
| Terminal structured turn | `agentic._terminal_response` | An unvalidated free-text answer; the loop always ends in `with_structured_output` |

A refused tool call returns a readable refusal **to the model**, not an exception:
it gets a chance to correct itself, the run survives, and the traveller's trip is
never quietly changed.

### What it actually buys, honestly

**Not** a reduction in Path 2. On seed, Path 2 is caused by an unstocked *airport
pair*, and no amount of re-searching creates rows that do not exist (§2's
correction).

What it buys is **empty → populated**. SIN↔NRT is stocked on only a handful of
dates inside the 42-day window, so a traveller asking for 12 September gets a
grounded, honest, empty answer today. The loop searches again a day or two either
side and returns real flights. Demonstrated end to end in
`test_widened_search_turns_an_empty_leg_into_real_options`.

That required one thing beyond the loop itself: a widened search that finds
flights must also be allowed to *keep* them. `ToolContext.resolved_request()`
carries the leg dates that actually produced results into the final proposal —
without it the loop finds flights on a nearby date and then discards them, because
`propose_flights` filters on exact date equality against the original request. A
leg that finds nothing keeps the traveller's own date, so the coverage warning
still names the date they asked for, and every moved leg is disclosed by
`date_shift_notes()`. Showing someone a different date without saying so would be
worse than showing them nothing.

On Duffel the loop *would* reclaim real Path 2, because there `covers()` is
routability-only and Path 2 means "the search came back empty" — which a retry can
genuinely fix. That is why `max_provider_calls` exists even though seed makes
searching free.

### Measured, and the default that came out of it

Run against `openai/gpt-4o` on the seed provider, 36 live runs, six scenarios,
two repeats — `scripts/flight_mode_eval.py`, full numbers in
`docs/flight_agent/mode-eval.md`.

| mode | median | options found | runs returning options | tool calls |
| --- | --- | --- | --- | --- |
| `structured` | 5377ms | 32 | 8/12 | 0 |
| **`auto`** | **6046ms** | **44** | **10/12** | 8 |
| `agentic` | 8525ms | 44 | 10/12 | 24 |

The per-scenario table is what decided the design. The loop's benefit is
concentrated almost entirely in **one** case: a stocked route on an unstocked date
went from **0 options to 6**. On every other covered scenario it produced an
identical option count for 2-4 extra seconds. Always-on would have taxed every
traveller for a benefit most never see.

Hence a third mode, **`auto`, now the default**: run the single-shot path, and open
the loop only when the deterministic search leaves a leg empty. The escalation test
is `_has_empty_leg`, pure Python over rows already in memory with no model call,
which is what makes it worth doing — the common path keeps single-shot latency
exactly (`plain` 2924ms vs 2934ms; `london_hub` 3658ms vs 5276ms), and only the
requests that would otherwise return nothing pay for the loop.

`auto` reaches `agentic`'s full benefit (44 options, 10/12 runs) for +669ms on the
median rather than +3148ms, at a third of the tool calls.

Because `auto` is the default, a model without tool calling would otherwise turn an
empty leg into a failed request. That is checked as a capability
(`hasattr(llm, "bind_tools")`) and traced as `agent_loop_unavailable`, rather than
caught as an exception, so the trace says what happened.

### Grounding, measured against a real model

- **120 options across 36 live runs, 0 fabricated.** Every option is built by
  `_candidate_to_option` from `proposal.candidates`, so this is a structural
  property; the measurement confirms nothing routes around it.
- **The spike is settled: keep code-built options.** Of the runs where the model
  named specific flights, **5 of 5** named exactly the shortlist the deterministic
  ranking had already chosen. It cited **0** flights the ranking had dropped and
  **0** that no provider returned. The model would have picked the same flights, so
  letting it select buys nothing and costs the strongest guarantee in the codebase.
  Revisit only if that agreement rate falls on a larger or more varied corpus.

### Still worth knowing

- **`max_provider_calls` bounds `fetch` calls, not supplier searches.**
  `duffel.fetch` fans out over airport pairs and can issue four billed POSTs per
  call, so 2 can mean 8 billed searches. Re-read it before enabling Duffel.
- The measurement is on **seed**, where searching is free. On Duffel the loop's
  latency and cost profile will differ, and `auto` limits exposure by opening the
  loop only when a leg is empty.

---

## 5. Tracing

`run_flight_agent` emits its own `agent_started`/`agent_completed` because it is
usable standalone — `scripts/demo_golden_scenario.py` and
`demo_multi_gap_relaxation.py` call it directly, with no graph and no Flask app.

Inside the node those duplicate the lifecycle events `agent.py` already records,
so `_InnerTracer` (`agent.py:81`) drops exactly those two and forwards everything
genuinely new: `agent_input_blocked`, `agent_llm_attempt_failed`,
`agent_relaxation_applied`, `agent_relaxation_rejected`, `agent_fallback`.

Trace details are **counts, not content** — `injection_count`, `high_bias_count`,
`toxicity_count`. Traveller text never enters the audit trail.

---

## 6. Guardrail coverage today — summary

Updated 2026-08-23 — the Path 2 column was the gap; it is now closed.

| | Path 1 (grounded) | Path 2 (prompt-only) |
| --- | --- | --- |
| Input injection / bias / toxicity | ✅ pre-model | ✅ pre-model (`make_preflight`) |
| Output bias / toxicity | ✅ | ✅ (`make_postprocess`) |
| Grounding (invented flight IDs) | ✅ | ✅ n/a — concrete options are stripped |
| Structured output schema | ✅ | ✅ |
| `SYSTEM_POLICY` + untrusted-data labelling | ✅ | ✅ |
| Unverified-data disclosure | n/a | ✅ forced |
| LLM classifier (L2) | ⚠️ shared, at the API boundary only | ⚠️ same |

The L2 classifier added on 2026-08-16 screens at the HTTP boundary and on the
orchestrator's output. **No specialist owns one**, so neither path is screened by
the agent itself — which is why `run_flight_agent`, when called standalone by the
demo scripts, currently has no L2 protection at all.

---

## 7. Proposed change (not built)

Give the agent a guardrail it owns, invokes and configures, covering **both**
paths. Full plan in `temp_flightAgentLLMGuard.md`. Shape:

- `flight_agent/guardrails_llm.py` — flight-specific classifier prompts beside the
  existing deterministic `guardrails.py`.
- Path 1: `run_flight_agent(..., guardrail=None)`, duck-typed like the existing
  `tracer` parameter — `reasoning.py` is deliberately langchain-free and must stay
  that way.
- Path 2: pass the `preflight`/`postprocess` hooks that `make_specialist_node`
  has always accepted and this call site has never used.
- `FLIGHT_GUARDRAIL_*` config overriding the global `GUARDRAIL_*`.

Cost: 2 guardrail calls per plan today → 4 typical, 8 worst case.

**This does not solve §3.** An LLM classifier screens for injection, bias and
policy violations; it cannot tell whether flight SQ632 exists. Fabricated
inventory on Path 2 is a separate problem needing a separate answer — most
plausibly option 3 above, enforced in the schema rather than in a prompt.

---

## 8. Open items

Closed on 2026-08-23:

- ~~**Path 2 is unguarded**~~ — both hooks are now wired (§3's decision block).
- ~~**Fabricated flight specifics on Path 2**~~ — option 3 adopted and enforced in
  `_forbid_concrete_options`.
- ~~**No test asserts anything about Path 2's screening**~~ —
  `tests/test_flight_path2_screening.py` (7 cases) and `tests/test_agent_parity.py`
  (5 × 2 agents). Both were verified to fail against the un-wired node before
  being kept, so they are regression tests rather than tests that happen to pass.

Still open:

- **Option 1's presentational half** (§3) — estimated output is still
  distinguished only by a warning list, not visually.
- `docs/progress.md` §3.3's guardrail table describes Path 1 only. A reader would
  reasonably conclude the agent is guarded in all cases.
- **`agent_relaxation_applied` records `relaxation.reason`** — model-generated free
  text in the audit trail, contradicting §5's "counts, not content". Pre-existing;
  worth narrowing to a reason code.
