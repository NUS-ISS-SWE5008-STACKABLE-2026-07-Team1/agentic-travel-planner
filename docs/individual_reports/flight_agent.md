# Flight Agent — Individual Report Sections (Draft)

**Owner:** Mark. Covers the two sections `draftProject.txt` §6 requires in every team
member's individual report: Explainable & Responsible AI, and Security Practices. Both
are agent-specific here, not the group report's system-level synthesis of all four.

Status: draft, grounded only in what's actually built as of 2026-07-19
(`flaskapp/travel_ai/agents/flight_agent/`: `schemas.py`, `domain.py`, `guardrails.py`, `prompts.py`,
`agent.py`, plus `tracing.py`'s hash-chain verifier). The LLM "brain" layer
(`agent.py`'s `run_flight_agent`) now exists and is mock-tested (no live API key in
this environment) — see the architecture note below before assuming "has an LLM call"
means "is fully wired into the real system."

**On whether Flight Agent is "an agent" (brain/memory/tools)**: as of this session,
yes in the narrow sense that matters — `agent.py` adds a genuine LLM reasoning layer
(rationale generation, escalation judgment, and — option B — a bounded relaxation of
Flight's *own* soft preferences that genuinely changes control flow) on top of the
deterministic tool (`domain.py`), with memory supplied as orchestrator-fed negotiation
history (`FlightProposalRequest.negotiation_history`) rather than agent-owned storage.

**Option B — the LLM's one place of real authority** (design rationale:
`localfolder/discussion_agents_vs_deterministic.md`): when every surviving flight on a leg still
violates one of the traveller's own soft preferences, the LLM may propose relaxing
exactly that preference (`avoid_red_eye`, `prefer_direct`, or a soft arrival
preference — never a hard constraint). The fence is enforced twice in code, not by
prompt wording: (1) `PreferenceRelaxation.field` is a Pydantic `Literal`, so the LLM
literally cannot express a request to touch accessibility/budget/seats/max_stops/a hard
arrival deadline; (2) `domain.relaxation_is_valid()` re-confirms a real gap exists
before applying, never trusting the LLM's claim. At most one extra tool call + one
extra LLM pass per run — bounded, not a loop. An empty leg (hard constraint removed
everything) is explicitly NOT relaxable — that path goes to `escalate`, not relaxation.
This is what makes the LLM non-decorative: a validated relaxation demonstrably reorders
the proposal (e.g. dropping a soft arrival preference reverts arrival-ranking to
price-ranking), so removing the LLM changes the outcome, not just the prose.

What's *not* yet true: this isn't wired into a real orchestrator, event bus, or live
LLM call — `agent.py` is structurally complete and correctly grounded/fail-closed, but
untested against a real model. Don't overstate this in the report as "fully working
end-to-end" — it's the reasoning layer, built and correct in isolation, not yet
integrated.

---

## Explainable & Responsible AI

### Explainability

Every proposal is traceable at two levels:
- **Why a flight was or wasn't proposed**: `domain.screen_flights()` runs the same
  filtering rules `propose_flights()` uses, but returns a `FlightScreeningResult` for
  *every* same-route inventory row — included or not, with the specific reason
  (`"price 520 exceeds constraint max_price 400"`, `"only 8 seat(s) available, party
  needs 10"`, etc.). This closes what would otherwise be a gap against
  `db_schema.md`'s explainability substrate requirement ("every claim traceable to a
  row") — rejections need a reason on record, not just survivors. Feeds the frontend's
  planned Explainability Drill-Down Panel directly.
- **Tamper-evidence on the trace itself**: `tracing.AuditTracer` hash-chains every
  lifecycle event; `tracing.verify_hash_chain()` (added this session) recomputes the
  chain and returns `False` if any event was edited or reordered after the fact. An
  explanation is only trustworthy if the log it's built from wasn't altered.

**Now built**: `agent.py`'s `run_flight_agent()` — the LLM reasoning layer. Deliberately
scoped narrow: the tool (`propose_flights`/`screen_flights`) always runs first and is
never bypassed; the LLM only writes the rationale, judges escalation, and suggests
(never decides) a relaxation for the next round — matches `llmops_plan.md` §8's
structured-output pattern for Flight, not a full autonomous tool-calling loop.
`guardrails.validate_grounded_explanation()` gates every response: a hallucinated
flight_id triggers a retry, then a deterministic fallback with no narrative — the tool's
output is always usable even if the LLM never succeeds. Mock-tested only; needs a real
`OPENAI_API_KEY` to exercise for real.

### Bias & Fairness

The system's core bias mechanism — the "disability tax" (wheelchair-requiring
travellers facing a smaller, pricier accessible-option set) — is Accessibility Agent's
(Person C's) scoped deliverable per `draftProject.txt` §7, since it's emergent from
agent *interaction*, not any single agent's bug. Flight Agent's honest contribution to
that emergent effect: `domain.py`'s first-round proactive filter drops any flight
without `wheelchair_assist_available=True` when `accessibility_needs` includes
"wheelchair" — this shrinks the candidate set Hotel/Accessibility ever see, before
their own filtering even runs. That's a deliberate design choice (fail early rather than
propose an infeasible flight), not a hidden one — it's visible in `screen_flights()`'s
output as an explicit reason (`"accessibility_needs includes wheelchair but
wheelchair_assist_available is False"`), so it's auditable by the profile-comparison
harness `llmops_plan.md` §4/§7 already plans (option-count parity across
Budget_Disabled vs. Budget_Solo profiles would show this filter's actual effect size).

**Seat selection extends the disability-tax surface (a deliberate bias decision).**
Seat availability is modelled as a flight-selection factor (`SeatInventory` — aggregate
counts, not a seat map). The bias-relevant choice: `accessible_available` seats are
**always free**, while EXTRA_LEGROOM and EXIT_ROW seats carry fees. Charging for
accessible seats — even at parity with extra-legroom — would *not* be fair, because the
disability tax is about a required accommodation being mandatory-not-optional: a
non-disabled traveller can always take the free standard seat, a wheelchair user for
whom the accessible seat is the only usable one cannot. Same fee, different meaning.
(Exit-row seats also legally exclude passengers needing wheelchair assistance, e.g. FAA
14 CFR 121.585, enforced as a hard filter.) A deliberately-charged accessible variant is
kept as an explicit "don't do this" case for Andrew's parity harness, not the default.

Flight Agent's own quality metrics, per `llmops_plan.md` §2a: first-pass feasibility
rate (% proposals passing Hotel's check-in check without renegotiation) target ≥70%;
structured-output validity target ≥95% (Pydantic parse success — not yet measurable
without a prompt/LLM call to parse).

### Governance (IMDA MGF mapping)

| MGF principle | Flight Agent evidence |
|---|---|
| Internal governance | `schemas.py`'s `extra="forbid"` Pydantic contracts reject any field an LLM might invent; `domain.py` has zero LLM calls, so ranking logic is 100% reviewable code, not a black box |
| Human involvement | Empty candidate list (both legs unfeasible) is a legible signal the orchestrator can escalate to the user — not yet wired to an actual escalation path since the orchestrator doesn't exist as working code yet |
| Operations management | `docs/prompt_specs/flight_agent.md`'s example cases are meant to become permanent `promptfoo`/golden-suite regression cases once the prompt exists, per `llmops_plan.md` §8's "every prompt bug becomes a permanent test case" |
| Stakeholder communication | `screen_flights()`'s per-rejection reasons are the raw material for the "why this option, not that one" narrative required in the final itinerary output |

## Security Practices

Agent-specific risks and mitigations, mapped to the 5-gate model
(`agenticainotes.md` §7). Adopted after reviewing `localfolder/XRAI/`'s guardrail
notebooks and checking what actually transfers from a governed-chatbot design to a
backend reasoning agent with no chat UI — see `localfolder/discussion_18Jul.md`.

| Risk | Gate | Mitigation | Status |
|---|---|---|---|
| Prompt injection via `trip_context.preferences` (Flight Agent's only free-text input — dates/airports/budget are structured and can't carry one) | Pre-tool | `guardrails.screen_preferences()`, precise patterns adapted from the XRAI notebook (2026-07-20, see below); wired into `run_flight_agent()` — a blocked input short-circuits before the LLM is ever called | Built and wired in |
| Biased/stereotyping input (e.g. a preference combining a protected-attribute mention with a stereotyping claim) | Pre-tool | `guardrails.detect_bias()` — two-tier: a bare attribute mention (e.g. a destination cuisine) is `medium` and NOT blocked, only attribute+stereotype combined is `high` and blocks | Built and wired in |
| Toxic input text | Pre-tool | `guardrails.detect_toxicity()`, keyword-fallback (not the ML `detoxify` backend — not worth the dependency for this scale) | Built and wired in |
| Biased/stereotyping or toxic LLM output (the rationale itself) | Post-tool | `guardrails.screen_output_text()` — same two detectors as the input side, applied symmetrically to `response.rationale`; flagged output is retried, then falls back, exactly like a hallucination | Built and wired in |
| Hallucinated flight_id/price in an LLM-generated explanation | Post-tool | `guardrails.validate_grounded_explanation()` — every mentioned id must exist in the actual proposal, enforced in `agent.py` on every call | Built and wired in |
| Rejections silently dropped, undermining the explainability requirement | Post-tool | `domain.screen_flights()` records a reason for every excluded row | Built |
| Audit trace tampered with after the fact | Post-response | `tracing.verify_hash_chain()` | Built |
| Overbooking — proposing a flight without enough seats for the whole party | Correctness/availability | Party-size-aware seat filtering in `domain.py` | Built this session (was a real bug: only checked `seats_available > 0`, not against party size) |
| Renegotiation constraint meant for one leg silently filtering the other leg too | Correctness/availability | `FlightConstraints.direction` scopes a constraint to the leg that triggered it | Built this session (was a real bug found while writing golden scenarios) |
| Resource exhaustion / abusive request volume | Pre-tool | Rate limiting | **Not built** — currently relies on whatever the orchestrator/API gateway does; Flight Agent has no rate limiting of its own |
| Inventory data poisoning (once a real DB/ingestion pipeline replaces static seed data) | Pre-tool / data quality | — | **Not applicable yet** — `seed_data.py` is static, version-controlled Python; revisit once real inventory ingestion exists |
| Fail-closed on internal error (explanation-LLM call throws or hallucinates) | Post-tool | `agent.run_flight_agent()` retries once, then falls back to a deterministic, still-grounded response — never returns ungrounded output, never blocks the tool's own result | Built |

**Resolved 2026-07-20**: the deferred item below was closed by reading
`localfolder/XRAI/AI Agent 5b Chatbot with Guardrails and Policy (ext yaml file).ipynb`
and its companion `.yaml` closely. Its `evaluate_input`/`evaluate_output` design — the
*same* `detect_bias`/toxicity detectors applied to both the user's question and the
generated answer — is reused close to verbatim (`screen_input_text`/`screen_output_text`
above). One documented adaptation, not a silent change: the notebook's injection pattern
requires a determiner (`ignore all/any/the previous instructions`); common injection
phrasing often omits it, so the determiner was made optional to keep catching the bare
form the existing test suite already covered, while still matching the source's more
specific variants. Known limitation carried over from the source: the bias
attribute/stereotype word lists are what the notebook shipped with, not exhaustively
expanded — e.g. "mosque"/"halal" aren't in the religion list even though "muslim" is;
expanding the vocabulary would be inventing content beyond what was verified from the
source, so it wasn't done unilaterally.
