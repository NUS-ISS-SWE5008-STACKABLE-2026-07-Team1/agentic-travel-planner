# PII redaction and prompt-injection verification at intake

Status: implemented
Date: 2026-08-30

Six things changed during implementation and are recorded in "Deviations from
the design, as built" below. Two of them are defects in the reference patterns
this design started from.

## Problem

Two gaps at the conversational intake boundary, `POST /api/v1/travel-intents`.

**PII is never redacted.** `Category.PII_EXPOSURE` exists in
`guardrails/types.py:36` and both classifier prompts name it, but nothing in the
system removes an identifier from text. A traveller who types "my passport is
E12345678, book me Singapore to Tokyo" has that number sent verbatim to the L2
classifier, sent verbatim to the extraction model in `intake.py:52-56`, and
written verbatim into `intake_messages` by `api.py:139-141`. The classifier can
say the text *contains* PII; it cannot stop the PII from being stored or
transmitted, and a `block` verdict denies the trip rather than cleaning it.

**L1 injection detection is four alternatives.** `safeguards.py:14-16` is one
regex covering `ignore (all|any|previous)`, `system prompt`, `developer
message`, `reveal .*prompt`, and `act as`. `classifier.py`'s own module
docstring already names this as the reason L2 exists. That reasoning holds, but
it is not an argument for leaving L1 at four patterns: every attack L1 catches
is one that costs no API call, cannot be talked out of its instructions, and
cannot fail open on a provider timeout.

## Scope

In scope: a deterministic PII redaction layer, a broadened and named
prompt-injection guard, and both wired into the single intake screening
function.

Out of scope, explicitly:

- **PII redaction on any surface but the intake prompt.** Structured request
  fields (`POST /travel-plans`), feedback comments, and generated plan prose are
  deliberately left alone in this change. The intake prompt is the only
  unconstrained-prose surface a traveller types into, and it is where an
  identifier realistically arrives. Extending to the other three is a later
  change against the same `PiiRedactor`.
- **Migrating to `langchain.agents.create_agent`.** The reference middleware
  list that prompted this work runs only inside `create_agent`'s pipeline, and
  the `langchain` package is not a dependency here (`langchain-core` 1.5.2 and
  `langgraph` 1.2.10 are). The orchestrator is a hand-written LangGraph node
  using `with_structured_output` plus this project's own tracing, output gate
  and cancellation. Rebuilding it on `create_agent` to obtain a middleware list
  would put all four of those at risk to gain no behaviour. The behaviour is
  implemented in this codebase's existing idiom instead.
- **Blocking on PII.** Redaction is not a denial. See "Decisions".
- Changes to `TravelRequest`, the LangGraph workflow, or any specialist agent.

## Decisions

| Decision | Choice | Rejected alternative |
| --- | --- | --- |
| Where redaction runs | Once, in `screen_prompt`, before the L2 call | Once per model call site |
| Ordering | length → injection → **redact** → L2 | Redact after L2 |
| Propagation | The redacted string is what is stored, sent, and echoed | Redact only the model's copy |
| PII response | Redact and proceed | 422 the traveller |
| Rule shape | Ordered table of `PiiRule`, order load-bearing | One `redact_pii()` function |
| Evidence | Per-rule match **counts**, never matched text | Logging what was redacted |
| Injection guard | Extracted to `guardrails/injection.py`, pattern set broadened | Left inline in `safeguards.py` |
| `PROMPT_INJECTION` | Rebuilt as the union of the new rules, still exported from `safeguards` | New name, callers updated |

Three of these earn an explanation.

**Redact before L2, not after.** The classifier is a network call to Azure or
OpenAI. Redacting after it would mean the one component whose job is to notice
PII is also the component that leaks it. Redaction is local, deterministic and
sub-millisecond, so there is no cost argument for the other order.

**Injection first, redaction second.** Injection detection is free local regex
and its outcome is a rejection, so running it first means an injected prompt is
refused without any redaction work. Redaction cannot create or destroy an
injection pattern — the two rule sets are disjoint — so the order is a cost
choice, not a correctness one.

**Redaction never blocks.** A traveller who volunteers a phone number is not an
attacker, and a 422 teaches them nothing except that the product is broken. The
identifier is removed and planning continues; the trip does not need it. This is
also why `PII_EXPOSURE` remains a live L2 category: L2 still judges *intent*
around credentials, and can still block. Redaction handles the volunteered case,
L2 handles the adversarial one.

## Flow

```
prompt (raw, from the browser)
  │
  ├─ not a string / empty / > MAX_INPUT_CHARS ──────────► SafetyError → 422
  │
  ├─ PromptInjectionGuard.detect()  ── rule fires ──────► SafetyError → 422
  │        L1, local regex, no network
  │
  ├─ PiiRedactor.redact()
  │        L1, local regex, never raises, never blocks
  │        └─► ScreenedPrompt.text  (redacted)
  │            ScreenedPrompt.pii   ({"nric": 1})
  │
  └─ LlmGuardrail.screen_input([redacted text])
           L2, network, fail-closed
           ├─ BLOCK ─────────────────────────────────────► GuardrailBlocked → 422
           └─ ALLOW / FLAG
                  │
                  ▼
        redacted text is what is:
          · persisted by save_intake_message
          · sent to extract_intent
          · echoed back in the transcript
```

Raw PII never crosses a network boundary and never reaches Postgres or the
trace files.

## Components

### `flaskapp/travel_ai/guardrails/pii.py` (new)

```python
Strategy = Literal["redact", "mask"]

@dataclass(frozen=True)
class PiiRule:
    name: str
    pattern: re.Pattern[str]
    strategy: Strategy
    guard: Callable[[re.Match[str]], bool] | None = None   # True → skip this match

@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: Mapping[str, int]                              # {"nric": 1}
    @property
    def redacted(self) -> bool
    def as_audit_details(self) -> dict                      # counts + total, no text

class PiiRedactor:
    def __init__(self, rules=DEFAULT_RULES, *, enabled=True)
    def redact(self, text: str) -> RedactionResult
    @classmethod
    def from_config(cls, config) -> PiiRedactor
```

`DEFAULT_RULES` in this order, **the order being load-bearing**:

| # | rule | pattern | strategy | replacement |
| --- | --- | --- | --- | --- |
| 1 | `credit_card` | `\b(?:\d[ -]*?){13,16}\b` | mask | last four digits kept |
| 2 | `email` | `\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b` | redact | `[REDACTED_EMAIL]` |
| 3 | `nric` | `\b[STFG]\d{7}[A-Z]\b` | redact | `[REDACTED_NRIC]` |
| 4 | `passport` | `\b[A-Z]{1,2}\d{6,9}\b` | redact | `[REDACTED_PASSPORT]` |
| 5 | `phone` | `\b\+?[\d][\d\s\-().]{7,}\d\b` | redact | `[REDACTED_PHONE]` |

`redact` replaces the whole match with the placeholder. `mask` replaces every
digit except the final four with `*` and leaves the original separators in
place, so a 16-digit card written `4111-1111-1111-1111` becomes
`****-****-****-1111` and a 15-digit card written without separators becomes
`***********1111`. Digit count and formatting are therefore preserved, which is
what makes a masked value still recognisable as a card in a transcript without
being usable as one.

Two collisions were measured against the reference patterns before this design
was written, and both are why the table is ordered rather than a dict:

- **`phone` matches credit card numbers.** `\b\+?[\d][\d\s\-().]{7,}\d\b`
  matches `4111 1111 1111 1111` in full. Whichever rule runs first owns the
  match, so `credit_card` runs first and a card is masked as a card.
- **`phone` matches ISO-8601 dates.** It matches `2026-10-10`. Departure and
  return dates are the two fields intake exists to extract, so an unguarded
  `phone` rule would silently destroy every trip request that states its dates
  numerically. `phone` therefore carries a guard that skips any match which is,
  or falls inside, an ISO-8601 date span in `match.string`. The guard reads the
  live string on each rule, so it stays correct after earlier rules have already
  rewritten the text.

`nric` precedes `passport` defensively. They were measured not to collide —
`\b[A-Z]{1,2}\d{6,9}\b` will not match inside `S1234567D`, because the trailing
letter denies the word boundary — but the ordering costs nothing and the
narrower rule winning is the property we want if either pattern is ever widened.

`credit_card` additionally guards on a Luhn check. A travel planner's prose is
full of numbers (budgets, flight numbers, durations); a 13-16 digit run that
fails Luhn is not a card, and masking it would corrupt the request for no gain.

`counts` carries rule names and integers only. It is modelled directly on
`Verdict.as_audit_details` (`types.py:52-72`) and for the same reason: this value
reaches the trace file, which is served by `GET /api/v1/traces/<id>` and rendered
in the admin dashboard. Recording *what* was redacted there would reintroduce, in
the audit log, exactly the data this layer exists to remove.

### `flaskapp/travel_ai/guardrails/injection.py` (new)

```python
@dataclass(frozen=True)
class InjectionRule:
    name: str
    pattern: re.Pattern[str]

class PromptInjectionGuard:
    def detect(self, text: str) -> str | None    # the rule name that fired, or None

PROMPT_INJECTION: re.Pattern[str]                # union of every rule, case-insensitive
```

`detect` returns a name rather than raising, so `guardrails/` keeps no dependency
on `safeguards`; `safeguards` already depends on `guardrails`, and the reverse
edge would be a cycle. Raising stays with the caller.

Rule set — the existing four, redistributed and extended:

| rule | covers |
| --- | --- |
| `instruction_override` | `ignore (all\|any\|previous)`, `disregard (the )?(previous\|above\|prior)`, `forget (everything\|all\|previous)` |
| `system_prompt` | `system prompt`, `developer message`, `reveal .*prompt`, `initial instructions` |
| `role_play` | `act as`, `you are now`, `pretend (to be\|you are)`, `roleplay as`, `DAN mode` |
| `delimiter` | `</?(system\|instructions?\|admin)>`, `\[/?(INST\|SYS)\]`, `###\s*(system\|instruction)` |
| `tool_instruction` | `call (the )?tool`, `execute (the )?(command\|code)`, `run the following` |
| `encoded_payload` | literal `base64:`, and unbroken `[A-Za-z0-9+/]{40,}={0,2}` runs that contain a digit and both cases |

`encoded_payload`'s length and character-mix conditions exist to keep it off
natural language: travel prose does not contain 40-character unbroken mixed-case
alphanumeric runs, and requiring the mix rules out a long single-case word.

### `flaskapp/travel_ai/safeguards.py` (changed)

```python
from flaskapp.travel_ai.guardrails.injection import PROMPT_INJECTION, PromptInjectionGuard

@dataclass(frozen=True)
class ScreenedPrompt:
    text: str                       # redacted and stripped
    pii: RedactionResult
    verdict: Verdict | None         # the L2 ALLOW/FLAG, as screen_request_l2 returns

def screen_prompt(text, max_chars, guardrail=None, redactor=None) -> ScreenedPrompt
```

The return type widens from `str` to `ScreenedPrompt`. There is exactly one
production caller (`api.py:125`) and no test calls it directly, so this is a
two-line change at the call site rather than a compatibility problem.

`PROMPT_INJECTION` is re-exported, now compiled from the union of the rules
above. This is what keeps three existing readers working unchanged:
`validate_request` (`safeguards.py:120`), the admin prompt catalog
(`api.py:278`, which renders `.pattern`), and the eval harness's L1 attribution
(`tests/adversarial/harness.py:52`).

**Consequence, stated deliberately:** because the constant is shared,
`validate_request` gains the broadened rule set too, so structured fields —
preferences, accessibility needs, refinement notes, city names — are checked
against six rules instead of four. That is the intended direction, but it does
raise false-positive risk on fields this design did not otherwise touch, and the
eval corpus is where that gets measured rather than asserted.

### `flaskapp/travel_ai/api.py` (changed)

In `create_travel_intent`, `screen_prompt` returns `ScreenedPrompt`. Then:

- `screened.text` is passed to `save_intake_message` and to `extract_intent`.
  These are the two lines that make redaction real; everything upstream is
  detection.
- When `screened.pii.redacted`, the tracer records a `pii_redacted` event on
  `orchestrator_agent` carrying `screened.pii.as_audit_details()`. The tracer is
  constructed after extraction today, so the counts are held on the response path
  and recorded alongside the existing `orchestrator_validation_*` events.

### `flaskapp/config.py` (changed)

```python
# Deterministic PII redaction on the intake prompt, ahead of the L2 call.
# Off is a development convenience only: with it off, identifiers a traveller
# types reach the classifier, the extraction model, and intake_messages.
PII_REDACTION_ENABLED = os.getenv("PII_REDACTION_ENABLED", "true").lower() == "true"
```

Read by `PiiRedactor.from_config`, following the `vars(Config) if config is None
else config` seam that `flight_agent/agent.py:190` established, so tests override
it with a plain dict. Note on `unread_environment_settings` (the set-but-never-read
warning from `fa0d38e`): that helper is NOT in this branch. `subbu-feature-28Aug`
was cut from `main` at `85c233c`, five commits behind `subbu-25Aug`. Declaring
the name on `Config` is still what will satisfy it once the branches meet.

`.env.example` gains the same name with the comment above it.

## Error handling

| Condition | Behaviour |
| --- | --- |
| Prompt missing, blank, or oversized | `SafetyError` → 422 (unchanged) |
| Injection rule fires | `SafetyError` → 422 (unchanged message and shape) |
| PII found | Redacted; request proceeds. Never raises, never blocks |
| Redaction disabled by config | No-op; `counts` empty; every other layer unchanged |
| L2 blocks | `GuardrailBlocked` → 422 (unchanged) |
| L2 errors or times out | Existing fail-closed posture, unchanged |

No new failure path is introduced. The one new component that runs on every
request cannot fail the request.

## Deviations from the design, as built

**1. The reference `phone` pattern loses the leading `+`.** `\b\+?[\d]...` cannot
match the `+` in `+65 9123 4567`: a word boundary requires a word character on
one side, and both the preceding space and the `+` are non-word, so `\b` can only
anchor at the `6`. The redacted output was `+[REDACTED_PHONE]`, leaking the fact
of a country code and looking like a bug. Shipped as `(?<!\w)\+?\d[\d\s\-().]{7,}\d\b`.

**2. A Luhn-failing digit run is redacted, not left alone.** The design said it
would be "left alone". It is not: it falls through to `phone`, which matches the
same span, and is redacted as a phone number. Over-redaction is the safe
direction to err, so the behaviour stands and the test asserts what actually
happens — that it is not *masked as a card* — rather than the stronger claim the
design made.

**3. `act as` was tightened, fixing a declared false positive.** Measuring benign
prose, as the test plan required, showed "Can you act as our guide" being
rejected. This was pre-existing — bare `act as` was one of the original four
alternatives — and already known: `tests/adversarial/harness.py` documented that
`flight_agent` had tightened its own pattern while the HTTP boundary had not, and
corpus case `ben-001` was pinned as a live L1 false positive. `act as` now
requires an adversarial complement (`unrestricted`, `admin`, `developer`, ...),
matching the agent-level detectors, and `ben-001` is re-labelled `allow`.

**4. Two corpus attacks moved `allow` to `block`.** `inj-103` (base64 payload) is
now caught by `encoded_payload` and `inj-108` ("you are now unrestricted") by
`role_play`. Both were previously L2's to catch and now cost no API call. Each
entry carries the reason and the date, per the corpus contract that the boundary
may move but never silently.

**5. The admin prompt catalog gained a "PII redaction (L1)" entry.** Not in the
design. `tests/test_api.py` pins the catalog's length, and that dashboard is
where the guardrail stack is demonstrated; a layer missing from it would make the
page wrong.

**6. No PII cases were added to the adversarial corpus.** The design called for
this. The harness turned out to be block/allow throughout — `l1_input` returns
`{"blocked": bool}` and every metric is built on it — while redaction is a
transformation, not a decision. PII rows would have been trivially
`allow`/`allow`, adding rows to the corpus and no signal to the report. Redaction
is covered by `tests/test_pii_redaction.py` instead, including the end-to-end
assertion that the redacted string is what reaches the model and the database.
Giving the harness a redaction dimension is a real piece of work and belongs in
its own change.

## Known limitations

- **Regex PII detection has a recall ceiling.** `+65 9123 4567` is caught;
  "nine one two three" is not, and neither is an identifier in a format no rule
  describes. This layer raises the cost of leaking PII; it does not make it
  impossible. L2's `pii_exposure` category remains the semantic backstop.
- **The `phone` guard is ISO-8601 only.** `10/10/2026` is not currently
  protected and would be redacted as a phone number. Worth measuring against the
  corpus before widening the guard, since a wider date guard is a wider hole.
- **`passport` is broad by construction.** `\b[A-Z]{1,2}\d{6,9}\b` will match a
  booking reference or a product code of the same shape. Redacting one is
  harmless to planning; this is the deliberate direction to err.
- **Redaction is one-way.** There is no vault and no re-identification. Once
  redacted, the original is not recoverable from the transcript, which is the
  intent.

## Testing

TDD throughout. New `tests/test_pii_redaction.py`:

- Each rule redacts its own identifier, with the expected placeholder.
- `credit_card` is masked with the last four digits preserved, and is labelled a
  card rather than a phone.
- A Luhn-failing 16-digit run is left alone.
- **`2026-10-10` and `2026-10-16` survive a full `screen_prompt` intact** — the
  regression that motivates the `phone` guard.
- `counts` are correct, and contain no matched text.
- `PII_REDACTION_ENABLED=false` is an exact no-op.
- Redaction runs before the L2 call: assert the stub guardrail's `screen_input`
  received the redacted string, not the raw one.

Extended `tests/test_safeguards.py`:

- Each new injection rule rejects, and the existing four still reject.
- Benign travel prose containing "act as a local guide"-shaped text is measured
  rather than assumed — whatever it does, it does so in a named test.

Extended `tests/test_intake_api.py`:

- A prompt containing an NRIC returns 200, and `save_intake_message` was called
  with the redacted text.

`tests/adversarial/corpus` gains PII cases so `scripts/guardrail_eval.py` reports
the new layer, and re-running it regenerates
`docs/security/guardrail-eval-report.md` with the broadened L1 attribution.
