# Risk & Advisory Agent — Capabilities Overview

For the cross-team touchpoint: what this agent does, what it needs, what it
returns, and how to work with it. Implementation detail lives in
`design.md`; this is the short version for people who won't read that.

---

## What it does

Surfaces travel-risk information for a trip — visa/entry rules, local laws,
health notes, seasonal weather risk, and named local events — and connects
related facts into one insight where they overlap (e.g. a seasonal weather
window landing on top of a major local event, which compounds both
disruption risk and price surge).

It does not book anything, does not search the open internet, and does not
invent facts: every claim it makes traces back to a reference-data row it
can point to.

## How it decides what to say

```
Trip request
    │
    ▼
Look up reference data for the destination
(visa/legal facts, seasonal risk windows, dated local events)
    │
    ▼
Model narrates and connects what was found — cannot add new facts
    │
    ▼
Every citation is checked against the data; anything unverifiable is dropped
    │
    ▼
Result returned
```

If the destination has no reference data, it falls back to a general
best-effort answer and says so explicitly — it never presents a guess as if
it were sourced.

## File layout — who does what

```
risk_advisory_agent/
├── agent.py        entry point: decides grounded vs. fallback, assembles the result
├── adapter.py       translates the shared trip request into this agent's own shape
├── schemas.py        this agent's own data shapes (RiskItem, RiskProposal, ...)
├── providers/         where reference data comes from (today: CSV files)
├── domain.py          looks up reference data — plain code, no model call
├── reasoning.py       the model narrates over what domain.py found
├── guardrails.py      this agent's own safety checks (see below)
└── prompt.py          the two prompts (grounded path / fallback path)
```

Each file has exactly one job, and the model only ever touches one of them
(`reasoning.py`) — every other file is plain, deterministic code.

## Step by step

1. **`agent.py`** receives the trip request and asks `adapter.py` to translate
   it into this agent's own shape, resolving the destination to an internal
   city key in the process.
2. **`agent.py`** checks whether reference data exists for that destination.
   - **No** → hands off to a shared fallback path (still screened by this
     agent's own `guardrails.py`, not skipped) and labels the result
     accordingly. Stop here.
   - **Yes** → continue.
3. **`domain.py`** filters the reference data for that destination and
   trip dates and returns a list of grounded facts. No model involved.
4. **`guardrails.py`** screens the traveller's own free-text input before
   it goes anywhere near the model.
5. **`reasoning.py`** sends that list (plus the traveller's screened
   preferences) to the model and asks it to prioritise, connect, and
   narrate — never to add anything new.
6. **`guardrails.py`** checks the model's answer: every fact it cited must
   be one `domain.py` actually returned, its wording must pass the same
   safety screen as step 4, and if the data says something was serious
   enough to flag, the model's answer must reflect that. Fails either
   check → retried once, then falls back to presenting `domain.py`'s facts
   with no narrative rather than trusting a response that didn't pass.
7. **`agent.py`** assembles the final result and returns it.

## Current coverage

Reference data exists today for five cities: **Singapore, Berlin, Tokyo,
Barcelona, and Washington, D.C.** Any other destination gets the honest
fallback described above, not an error — but treat those five as the ones
to use for a reliable, fully-grounded demo.

## What it needs from you

Nothing beyond what's already in a standard trip request: destination,
travel dates, and (optionally) the traveller's stated preferences. No
special setup, no separate call — it reads the same shared trip request
every other specialist reads.

## What it returns

The same shared `AgentFinding` shape every specialist returns:

- **summary** — a short, plain-language narrative
- **options** — one per risk item found (title, detail, category, severity,
  source), so a caller can list or filter them individually rather than
  only reading the prose summary
- **warnings** — includes an `ESCALATE:`-prefixed entry when reference data
  marks something high-severity for these dates; treat that as "a human
  should see this before the plan is finalised regardless of anything else"
- **confidence** — 0 when the answer is a disclosed fallback, so a caller
  can tell a grounded answer from a best-effort one at a glance

## How to interact with it

No special integration needed — it plugs into the existing shared workflow
like every other specialist, and it is already reachable through the
project's Google A2A layer (it appeared in the shared agent registry from
day one, so it picked up an A2A endpoint automatically, with no extra work
on this agent's side).

The one thing worth knowing if you're consuming its output programmatically:
it never negotiates or revises — it's an evaluator, not a proposer. It
produces one finding per request; it doesn't take follow-up refinement
requests the way Flight or Hotel do.

**It doesn't run on every request.** `flaskapp/travel_ai/dispatch.py` decides
which specialists a request needs based on `plan_scope`: this agent runs for
a full trip or a flights-only request, but not for a hotel-only one — a
hotel-only request collects no departure country, so there's nothing for it
to reason about entry from. Not this agent's own logic; if you're wondering
why a response has no Risk & Advisory finding, check the request's scope
before assuming something broke.

## Known limitation, stated plainly

Coverage is five cities today. Anything outside that list gets a
clearly-labelled best-effort answer, not a grounded one — worth knowing
before picking a demo destination, or before trusting an answer for
anything outside those five in front of an audience.
