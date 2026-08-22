# Flight Agent — design notes

Written 2026-08-16. Describes the agent **as it is today**; the guardrail
refactor sketched in §7 is proposed, not built. No code was changed to produce
this document.

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

- an origin the inventory doesn't stock,
- dates outside the dataset's window,
- an unroutable country or city,
- a live Duffel search that failed, timed out, or came back empty.

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

| | Path 1 (grounded) | Path 2 (prompt-only) |
| --- | --- | --- |
| Input injection / bias / toxicity | ✅ pre-model | ❌ none |
| Output bias / toxicity | ✅ | ❌ none |
| Grounding (invented flight IDs) | ✅ | ❌ inapplicable |
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

- **Path 2 is unguarded** (§6) — the same gap `risk_advisory_agent` had, inside
  the agent with otherwise the best guardrails in the codebase.
- **Fabricated flight specifics on Path 2** (§3) — needs a recorded decision.
- No test asserts anything about Path 2's screening, which is why the gap
  survived. A regression test here is worth more than the fix.
- `docs/progress.md` §3.3's guardrail table describes Path 1 only. A reader would
  reasonably conclude the agent is guarded in all cases.
