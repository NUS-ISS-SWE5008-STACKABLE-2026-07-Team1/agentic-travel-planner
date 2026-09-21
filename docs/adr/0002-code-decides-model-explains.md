# ADR 0002: Code decides, the model explains

**Status:** Accepted — the governing constraint of the Flight and Hotel agents

## Context

A traveller acts on what this system outputs. A fabricated flight number, a
wrong fare, or an invented hotel is not a wording problem — it is the system
being confidently wrong about something checkable.

LLMs are good at explaining a choice in prose and unreliable at *being* the
system of record for facts.

## Options

1. **Model as decision-maker** — hand the model the inventory and let it pick.
2. **Model as explainer** — Python searches, filters and ranks; the model is
   given the finished proposal and asked only for the rationale.
3. **No model** — deterministic output only, templated prose.

## Decision

Option 2. `domain.py` is pure functions with no I/O and no langchain: hard
filters, a lexicographic rank key, relaxation-gap detection. `agent.py` builds
every `Option` from `proposal.candidates`, which come from rows a provider
actually returned. The model's message never becomes a flight.

The test of the principle: **removing the model degrades the wording, not the
candidate list.**

Option 1 was rejected because there is no way to make it safe after the fact.
Option 3 was rejected because the explanation is a real deliverable — the
course requires stakeholder-facing reasoning, and `screen_flights()`'s rejection
reasons are raw material, not prose.

## Enforcement — three layers, none of them the prompt

1. **Structural.** Options are constructed from candidates, so a fabricated
   flight is *absent by construction* rather than filtered out afterwards.
2. **Grounding validation.** `validate_grounded_explanation()` rejects a
   rationale naming an ID not in the proposal; retry, then fallback.
3. **A typed fence on the model's one real power.** `PreferenceRelaxation.field`
   is a three-value `Literal`, so the model cannot even express a request to
   relax budget, `max_stops`, a hard arrival deadline, or accessibility.
   `domain.relaxation_is_valid()` then re-verifies a genuine gap exists rather
   than trusting the model's claim.

## Consequences

- Measured: 120 options generated against a live model, **0 fabricated**
  (`docs/flight_agent/mode-eval.md`).
- The model is not decorative. A validated relaxation demonstrably reorders the
  proposal, so removing it changes the outcome, not only the prose.
- Where no inventory covers a route there is nothing to ground against, so that
  path must strip options entirely — see [ADR 0011](0011-degrade-visibly.md).
- Ranking quality is capped by `domain.py`'s rank key. The model cannot rescue a
  bad ordering, only describe it.
