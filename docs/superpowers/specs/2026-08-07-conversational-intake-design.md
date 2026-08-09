# Conversational trip intake

Status: implemented
Date: 2026-08-07

Three things changed during implementation and are recorded in "Deviations"
below: hints are static rather than model-written, provider-client construction
was extracted to `travel_ai/llm.py`, and `TestConfig` had to be made
credential-free.

## Problem

`/main` collects a trip through a structured form: two country dropdowns, two
dates, traveller count, budget, and per-traveller age, gender and accessibility
fields. A user who already knows what they want has to translate that intent into
eight-plus controls before anything happens.

The goal is to let them type it instead — "I'm planning to go to Tokyo for 2 weeks
with my partner in October" — and have the orchestrator work out what it still
needs, ask for exactly that, and then delegate to the four specialists as it does
today.

## Scope

In scope: natural-language intake, deterministic gap detection, a clarification
card, and handoff to the existing planning pipeline.

Out of scope, explicitly:

- **Booking.** The system proposes; it never books. `assess_plan` asserts "No
  booking or safety guarantee is made" and every specialist returns estimates.
  "Delegate to sub agents to book" is implemented as delegation to the existing
  fan-out that proposes flights, hotel/transport and advisory findings.
- Changes to `TravelRequest`. The conversational path collects the full set of
  fields the schema requires today.
- Changes to the LangGraph workflow, `/chat`, or any specialist agent.

## Decisions

| Decision | Choice | Rejected alternative |
| --- | --- | --- |
| Intake UI | Prompt box primary; existing form retained as a collapsed fallback | Replacing the form outright |
| Required fields | Everything `TravelRequest` requires today, including per-traveller age and gender | Relaxing the schema to trip essentials |
| Clarification UX | One card listing every gap at once | One question per turn |
| Placement | Orchestrator-owned step before the graph | An interruptible node inside the graph |
| Inference | Ask, never assume — no date derived from a month or a duration, no origin derived from the profile | Proposing assumed values for confirmation |
| City names | Keep the city as stated (`destination: "Tokyo"`) | Resolving the city to a country |

Two further calls made during design review:

- **Two endpoints, not one.** Extraction needs the model; filling gaps does not.
  Splitting them means round two costs no tokens and is testable without a model.
- **A separate `intake.js`.** `app.js` is 1039 lines already covering planner,
  chat and admin. Intake is a fourth concern and gets its own file.

## Flow

```
prompt
  │
  ▼  POST /api/v1/travel-intents        model call
  │  { prompt }
  ▼
{ question, extracted, missing[], complete: false }
  │
  ▼  render intake card
  │
  ▼  POST /api/v1/travel-intents/resolve    no model call
  │  { extracted, answers }
  ▼
{ complete: true, request: {...} }
  │
  ▼  POST /api/v1/travel-plans          unchanged
  ▼  /chat/<id>                         unchanged
```

Partial intake state lives in the browser between rounds and is resent each time;
the server persists nothing until a plan is submitted. This mirrors the existing
`sessionStorage["atlas-plan-payload"]` handling in `app.js`.

The loop terminates by construction: `answers` only ever adds to `extracted`, so
the gap list shrinks monotonically. A field the user leaves blank simply reappears,
which is the same behaviour as an unfilled required form input.

## Components

### `flaskapp/travel_ai/agents/orchestrator_agent/intake_schemas.py`

```python
Gender = Literal["female", "male", "non_binary", "prefer_not_to_say"]  # as in schemas.TravelRequest


class ExtractedIntent(BaseModel):
    """A partial TravelRequest. Every field is optional by design."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    return_date: date | None = None
    travellers: int | None = None
    budget: float | None = None
    currency: str | None = None
    preferences: list[str] = Field(default_factory=list)
    accessibility_needs: list[str] = Field(default_factory=list)
    risk_tolerance: Literal["low", "medium", "high"] | None = None
    traveller_ages: list[int | None] = Field(default_factory=list)
    traveller_genders: list[Gender | None] = Field(default_factory=list)
    traveller_accessibility_needs: list[list[str] | None] = Field(default_factory=list)


class MissingField(BaseModel):
    name: str                      # "departure_date"
    label: str                     # "Departure date"
    input: Literal["country", "text", "date", "number", "gender"]
    traveller_index: int | None = None
    hint: str | None = None        # "you mentioned about 2 weeks in October"


class IntentResponse(BaseModel):
    complete: bool
    question: str                  # the assistant's chat bubble
    extracted: ExtractedIntent
    missing: list[MissingField]
    request: dict | None = None    # populated only when complete
```

`None` inside the per-traveller lists marks a slot that is still a gap, which is
what lets `travellers=2` with one known age produce exactly one indexed
`MissingField`.

### `flaskapp/travel_ai/agents/orchestrator_agent/intake_prompt.py`

`INTAKE_INSTRUCTION`, whose governing rule is that the model emits `null` for
anything not explicitly stated. It does not derive a date from a month or a
duration, does not derive origin from context, and does not resolve a city to a
country. It also writes `question`: one sentence acknowledging what it understood.

### `flaskapp/travel_ai/agents/orchestrator_agent/intake.py`

```python
def extract_intent(llm, prompt: str) -> tuple[ExtractedIntent, str]:
    """The single model call. Returns the partial intent and the acknowledgement."""

def compute_gaps(extracted: ExtractedIntent) -> list[MissingField]:
    """Pure. Walks the TravelRequest required set and expands per-traveller slots."""

def merge_answers(extracted: ExtractedIntent, answers: dict) -> ExtractedIntent:
    """Pure. Applies card values, including indexed per-traveller entries.

    `answers` is flat and keyed by MissingField.name, with per-traveller fields
    suffixed by their index:

        {"origin": "Singapore", "departure_date": "2026-10-10",
         "traveller_ages.0": 34, "traveller_genders.1": "female"}

    Blank and absent values are ignored rather than written as nulls, so an
    unanswered field stays a gap instead of overwriting a known value.
    """

def to_request_payload(extracted: ExtractedIntent) -> dict:
    """Pure. Builds the TravelRequest payload once no gaps remain."""
```

The division of labour is the point: **the model extracts and acknowledges; it
never decides what is missing.** Gap detection is ordinary Python against the
schema's required set, so the behaviour that matters most for "everything the
schema requires today" is deterministic and testable without a model.

`compute_gaps` derives the per-traveller expansion from `travellers`. Until
`travellers` itself is known, it reports `travellers` as a gap and emits no
indexed fields; once known, it emits `travellers` × 3 indexed gaps minus whatever
was extracted. If a later answer lowers `travellers`, `merge_answers` truncates
the per-traveller lists to the new length.

### API — added to the existing `travel_api_bp`

Both endpoints inherit the blueprint's `require_browser_session` guard and CSRF
protection.

| Endpoint | Model call | Request | Response |
| --- | --- | --- | --- |
| `POST /api/v1/travel-intents` | yes | `{prompt}` | `IntentResponse` |
| `POST /api/v1/travel-intents/resolve` | no | `{extracted, answers}` | `IntentResponse`, with `request` when complete |

`request` is a suggested payload, not a pre-validated one. The browser posts it to
`POST /api/v1/travel-plans`, which runs `validate_request` exactly as it does
today. Intake never becomes a route around the safeguards.

Error responses follow the existing convention: 401 unauthenticated, 422 for
`SafetyError` and `ValidationError`, 503 when no LLM credential is configured
(reusing `get_llm_settings`), 502 on an unexpected failure.

### Safeguards

The free-text prompt is a new injection surface. `validate_request` screens only
`preferences`, `accessibility_needs` and `refinement_notes`, none of which exist
at intake time.

- New `screen_prompt(text: str, max_chars: int) -> str` in `safeguards.py`:
  enforces the size cap and the existing `PROMPT_INJECTION` pattern, raising
  `SafetyError` **before** the model is called.
- The prompt enters the extractor as `"User request (untrusted data):\n..."`
  beneath `SYSTEM_POLICY`, framed exactly as `agents/base.py` frames specialist
  input.
- `SENSITIVE_KEYS` are rejected on `answers` in the resolve endpoint.

### Frontend

- `main.html` gains a prompt textarea and a submit button above the planner card.
  The existing form moves, markup unchanged, inside
  `<details><summary>Prefer to fill in a form instead?</summary>`. Keeping the
  markup intact preserves `test_valid_login_redirects_to_main`, which asserts on
  `"Select departure country"`.
- New `flaskapp/static/js/intake.js`, loaded only by `main.html`. It renders
  `MissingField[]` into inputs keyed by `input` type, reusing the `countries`
  list already passed to the template and the gender options that
  `renderTravelerFields` uses.
- On `complete`, it hands the payload to the existing plan-submission path and
  the browser redirects to `/chat/<id>`. `chat.html`, its JS, and the admin page
  are untouched.

## Error handling

| Case | Behaviour |
| --- | --- |
| Prompt is empty or whitespace | Client-side `required`; server returns 422 |
| Prompt exceeds `MAX_INPUT_CHARS` | 422 from `screen_prompt`, before any model call |
| Injection-like prompt | 422 from `screen_prompt`, before any model call |
| Model returns malformed output | Pydantic `ValidationError` → 422 with a message inviting the form fallback |
| Model call times out | 502 `retryable: true`, matching `create_travel_plan` |
| Sensitive key in `answers` | 422 listing the rejected keys |
| Answer fails type validation | 422; the card re-renders with that field still listed as a gap |
| Return date before departure date | Reported as a gap on `return_date`, hinted with the departure date. Never reaches `/travel-plans` |
| `complete` payload rejected by `validate_request` | The 422 surfaces in the intake area **and the card stays on screen with its answers**; only a successful submission removes it |

## Deviations from the design, as built

- **`MissingField.hint` is static helper text, not model-written.** The design
  imagined a hint like "you mentioned about 2 weeks in October". Sourcing that
  from the model would mean trusting it to describe its own omissions. The
  acknowledgement sentence already carries that context — the live run produced
  "You're planning a trip to Tokyo with your partner in October for about two
  weeks, correct?" — so hints stayed as fixed guidance such as "Leave blank if
  none".
- **`flaskapp/travel_ai/llm.py` is new.** `TravelPlanningService.create_plan`
  built its chat client through an eight-branch inline conditional. Intake needs
  the same client, so that block moved to `build_llm` and both callers use it.
  Adding a provider is now one edit rather than two.
- **`tests/test_api.py::TestConfig` now clears every provider credential.** It
  cleared only `AZURE_OPENAI_API_KEY`, so `test_plan_endpoint_requires_key`
  passed or failed depending on whether the developer had a working `.env` —
  it reads the process environment at import. Unrelated to intake, but it made
  the suite red as soon as the app was configured to run.
- **`question` round-trips through the client.** `/resolve` echoes back whatever
  question it is given rather than regenerating one, which is what keeps that
  endpoint free of model calls.

## Known limitations

- **Intake token usage will not appear in `/admin`.** `AuditTracer` and
  `save_agent_run` are keyed by a `request_id` that does not exist until a plan is
  submitted. Minting a throwaway ID to carry intake usage was rejected as worse
  than the gap. Admin monitoring therefore under-reports total spend per user by
  one extraction call per planning session.
- No intake trace file is written, for the same reason.
- Re-editing the original prompt after the card has rendered costs a second
  extraction call; the client discards accumulated answers in that case rather
  than trying to reconcile two extractions.

## Testing

`tests/test_intake_gaps.py` — pure functions, no model:

- empty extract yields the full required-set gap list
- `travellers=2` with one known age yields two indexed gaps at indices 0 and 1
- `merge_answers` is monotonic: known fields never revert to gaps
- lowering `travellers` truncates the per-traveller lists
- a complete extract yields `missing == []` and a payload `TravelRequest` accepts

`tests/test_intake_api.py` — model stubbed with `monkeypatch`, matching the style
already used in `tests/test_api.py`:

- 401 for an anonymous caller on both endpoints
- 422 for an injection-like prompt, an oversized prompt, and sensitive keys
- resolve round-trip returns `request` once complete
- 503 when no LLM credential is configured

`tests/test_safeguards.py` gains coverage for `screen_prompt`.

One "ask, never assume" test with a stubbed extractor asserts that
`"2 weeks in October"` leaves `departure_date is None` and lists it as a gap.
