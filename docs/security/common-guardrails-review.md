# Common guardrails — review and wrap-up

Written 2026-09-08. Covers the shared/system-wide guardrail layer — the part
that runs before any specialist agent, and the part that runs on the final
plan before it reaches the traveller. Does not cover any individual agent's
own screening (Flight/Hotel/Accessibility/Risk each have, or will have,
their own — those are reviewed separately by their own owners).

## What exists today

| Layer | What it does | Runs where |
|---|---|---|
| L0 — `schemas.py` | Structural validation (types, ranges, required fields) | Every request |
| L1 — `guardrails/injection.py` | Regex-based prompt-injection detection, 6 rule families (instruction override, system-prompt probing, role-play, delimiter injection, tool-instruction, base64/encoded payload) | HTTP intake boundary (`safeguards.py`) |
| L1 — `guardrails/pii.py` | Deterministic PII redaction (credit card, email, NRIC, passport, phone), ordered rules, redacts rather than blocks | HTTP intake boundary only |
| L2 — `guardrails/classifier.py` | LLM semantic classifier, separate input/output gates, structured verdict, fail-closed, cached | Once on raw request intake, once on the orchestrator's final plan |

This is a sound defense-in-depth design: cheap deterministic checks run
first and can reject on their own; the expensive semantic check runs last,
only on what already passed. PII is handled by redaction (safe to let
through, minus the identifier) rather than blocking, which correctly treats
a traveller volunteering a phone number differently from someone probing for
a credential. The eval harness (`scripts/guardrail_eval.py`, `docs/security/
guardrail-eval-report.md`) reports real precision/recall numbers, including
known false positives, rather than an unverified claim.

## Gaps found

**1. Bias/toxicity detection has no canonical shared home.** The regex
patterns and word lists live inside `agents/flight_agent/guardrails.py` —
one specific agent's own file — and are reached by other agents importing
from it (`accessibility_agent/guardrails.py`, and `risk_advisory_agent` via
`guardrails/specialist.py`). Unlike injection detection and PII redaction,
which both have a proper home in the shared `guardrails/` package, bias and
toxicity detection do not.

**2. `flight_agent/guardrails.py`'s own injection patterns are stale
relative to the HTTP-boundary rules.** Verified directly: it still has the
original 4 rule families and is missing `role_play`, `delimiter`,
`tool_instruction`, and — the significant one — `encoded_payload` (base64).
The HTTP-intake gate already screens the raw request with the broadened
6-rule set before any agent runs, so this is a second, weaker line of
defense rather than the primary one, but it is a real inconsistency. Fixing
it means editing `agents/flight_agent/guardrails.py`, which belongs to that
agent's owner — flagged here for them to action, not changed by this review.

**3. Homoglyph and invisible-character substitution had no defense.**
`guardrails/prompts.py`'s own L2 instructions already name this attack class
("smuggled through encoding... homoglyphs, spaced-out letters"), and
`encoded_payload` closed the base64 case, but nothing normalized text before
handing it to a regex. `"іgnore previous instructions"` (Cyrillic `і`) or
`"ig​nore previous instructions"` (zero-width space splitting the word) pass
every existing regex-based detector unmatched.

**4. No deterministic check for system-prompt leakage.** `system_prompt_leak`
is an L2 category, which means it is a semantic guess, not a fact. Nothing
in the pipeline proves a leak happened.

## What this wrap-up adds

Three new, self-contained modules in `guardrails/`, each with its own tests.
None of them modify any existing file — they are additions, not changes, and
none of them are wired into the live request pipeline yet (see "Next steps"):

- **`guardrails/detectors.py`** — a canonical, agent-neutral home for the
  bias/toxicity detectors that answers gap 1. Existing per-agent screening
  is untouched; this is offered as the reference implementation for any
  agent that wants to fork a known-good starting point instead of importing
  from `flight_agent`.
- **`guardrails/normalization.py`** — `normalize_for_screening()`, a
  pre-processing step (Unicode NFKC fold, zero-width stripping, homoglyph
  substitution) that answers gap 3. Meant to run ahead of any regex-based
  detector, not as a detector itself.
- **`guardrails/canary.py`** — a per-request secret token embedded in a
  system prompt, checked for verbatim appearance in generated output.
  Answers gap 4 with a deterministic, independently-reliable signal
  alongside L2's semantic guess.

## Integration (done in a follow-up pass)

`normalize_for_screening()` and the canary are now wired into the two
existing common chokepoints — the smallest change that makes both modules
real rather than unused:

- **`safeguards.py`**: `screen_prompt()` and `validate_request()` both run
  detection against a normalized copy of the text. The original text is
  still what gets redacted, stored and echoed — normalization is for
  detection only, verified by
  `test_screen_prompt_stores_the_original_text_not_the_normalized_copy`, so a
  genuine Cyrillic place name is never altered.
- **`orchestrator_agent/agent.py`**: a fresh canary token is embedded in the
  system prompt for every planning request; a leak in the generated plan is
  treated the same as an L2 `BLOCK` — retried once, then withheld — and
  recorded as `guardrail_canary_leak_detected` (never the token itself).
  Covered by `tests/adversarial/test_orchestrator_output_gate.py`, including
  a case with no L2 guardrail configured at all, to prove the canary is an
  independent check rather than one that only agrees with L2.

Both changes are additive to existing control flow — for a request that
triggers neither, behaviour is unchanged, confirmed by the full existing
suite passing unmodified (762 passed).

`guardrails/detectors.py` is deliberately left unwired. Wiring it in does
not require touching `agents/flight_agent/guardrails.py` itself — Flight's
own internal use of its own functions is unaffected either way. It requires
changing the import statement in whichever file currently reads from that
module instead of from here: `accessibility_agent/guardrails.py` (out of
scope — that is a different agent's own file, owned by Accessibility's
developer, not this review) and `guardrails/specialist.py` (in scope, but its
only current caller, `risk_advisory_agent`, is about to get its own
dedicated guardrails module and stop calling it — wiring this one now would
be short-lived).

## Still open

- Gap 2 (`flight_agent/guardrails.py`'s stale, narrower injection rule set)
  needs action from Flight Agent's owner, not from this review.
- Each individual agent's own guardrails (bias/injection/toxicity screening
  specific to its own domain) is out of scope here by design — see each
  agent's own review/design notes.
