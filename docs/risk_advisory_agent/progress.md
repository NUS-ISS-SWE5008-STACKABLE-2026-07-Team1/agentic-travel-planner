# Risk & Advisory Agent — Quick Sync

**Owner:** Adler
**Branch:** `Adler`
**Date:** 2026-08-10

## Done

- **Reference data** (`seed_data.py`): a small curated table of risk facts —
  visa notes, safety advisory level, health notes, seasonal risks (e.g.
  typhoon/bushfire/monsoon season) — for the 5 destinations Flight Agent
  already supports (Japan, UK, Australia, Thailand, Singapore). Using mock
  reference data instead of a live API, same call Andy made for hotels — no
  budget/time for a paid advisory feed, and it makes every fact auditable.
  Any destination outside these 5 honestly returns "no data" instead of a
  guess.

## Left to do

1. **Guardrails** — stop the LLM from stating a risk fact outside the
   reference table, and enforce that severe risks are always flagged
   `ESCALATE:`.
2. **Prompt rewrite** — walk the model through each risk category
   (visa/weather/safety/health/events) and require it to cite which
   reference entry backs each claim.
3. **Wiring** — connect the reference data + guardrails into `agent.py`.
4. **Tests** — data lookups, guardrail rejections, escalation cases.
5. **End-to-end check** — run the app, submit a real request, confirm the
   findings show up correctly in the final plan.
6. **Write-up** — individual report + reflection for submission.

## Blockers

None currently.
