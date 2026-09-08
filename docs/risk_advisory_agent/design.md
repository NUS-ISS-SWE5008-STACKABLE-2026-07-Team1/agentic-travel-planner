# Risk & Advisory Agent — Design v1

Written 2026-08-24. A proposal for the team to review before building.
No existing code was changed to produce this document.

---

## 1. What this agent does, end to end

One request goes through five steps. Nothing here talks to an outside model
or an outside API except step 3.

```
1. TravelRequest arrives
   (origin, destination, departure/return dates, budget, traveller profile)
        │
        ▼
2. Look up three local reference tables
   - Does the traveller's origin country need a visa for the destination?
   - Is the travel period inside a known seasonal-risk window (typhoon,
     monsoon, wildfire season...) at the destination?
   - Is there a known local event (festival, holiday closure...) overlapping
     the travel dates at the destination?
   → produces a list of RiskItem, e.g. "visa: not required, 90 days",
     "seasonal: typhoon tail season, medium severity",
     "local_event: autumn festival, price surge expected"
   This step is plain Python. No model call. The three items above are a
   fact, not a guess — they either match a table row or they don't.
        │
        ▼
3. A model reads that list and writes the traveller-facing explanation
   Its only job is to pick what matters, put it in plain language, and —
   where useful — connect two items into one insight (e.g. "typhoon season
   overlaps a large festival the same week, so hotel prices are likely up
   and rooms harder to find, not just one or the other").
   It is NOT allowed to state a risk that isn't in the list from step 2.
        │
        ▼
4. Check that the model didn't make anything up
   Every specific risk the model's explanation refers to must be traceable
   back to a row from step 2. Anything it mentions that isn't there gets
   dropped before it goes any further.
        │
        ▼
5. Package the result as an AgentFinding
   The traveller-facing explanation + the underlying risk list + a
   confidence score + (if severe enough) an escalation flag. This is what
   goes to the Orchestrator, alongside Flight/Hotel/Accessibility's findings.
```

**Why step 2 exists at all, instead of just asking the model:** right now
(see §2) there is no step 2 — the model is asked directly, with nothing to
check its answer against, so "this country requires a visa" or "there's a
typhoon risk in October" is something the model is inventing on the spot,
indistinguishable from something it actually knows. Step 2 gives it a fixed,
inspectable set of facts to work from, and step 4 makes sure it doesn't add
its own.

---

## 2. What exists today, and why it needs to change

`risk_advisory_agent/` is currently two files: an 11-line `agent.py` that
just asks a model to answer freely, and a 7-line prompt. There is no step 2,
no step 4 — the model's claims about visas, weather, or events are never
checked against anything, because nothing exists to check them against.
Fabricated travel-safety information is a real-world hazard, not just a
quality problem: a traveller who trusts a wrong visa or seasonal-risk claim
can end up denied entry or caught in weather they weren't warned about.

---

## 3. The three reference tables (step 2's data)

All three are written by the team, not fetched from anywhere — every value in
them is illustrative, not a live regulatory fact. That has to stay visible
to the traveller: every `RiskItem` produced from these tables carries a
`source` note saying so.

| Table | What it holds | Looked up by |
|---|---|---|
| `VISA_TABLE` | origin country, destination country, whether a visa is required, max stay | (origin, destination) |
| `SEASONAL_RISK_TABLE` | destination country, month range, risk type (typhoon/monsoon/...), severity | (destination, travel dates) |
| `LOCAL_EVENT_TABLE` | destination country/city, event name, date range, effect (price surge/crowding/closure) | (destination city, travel dates) |

One assumption worth stating plainly: the visa lookup uses the traveller's
*origin country* as a stand-in for their *passport nationality* — the two
aren't always the same person's same thing (someone can fly from Singapore on
a different country's passport), and the request form has no separate
nationality field today. This isn't a new problem this design introduces, but
it's never been written down before, and it changes how much weight a reader
should put on the visa result.

---

## 4. Making sure the model can't say something it wasn't given

Two checks run around the model call, same idea as the checks already used
elsewhere in the codebase for Flight and Hotel:

- **Before the call**: the traveller's own free-text input (preferences,
  notes) is scanned for injection attempts, bias, and toxic language. If it
  fails, the model is never called with it — a safe placeholder answer is
  returned instead.
- **After the call**: the model's generated explanation is checked two ways —
  scanned the same way as the input for bias/toxicity/injected text, and
  checked that every specific risk it names actually came from step 2's
  table lookups (the grounding check from §1 step 4). If either check fails,
  it retries once, then falls back to showing the plain table results with
  no narrative rather than showing something unverified.

If the destination isn't covered by any of the three tables at all, the
agent falls back to today's plain-prompt behaviour, but with a clear warning
attached saying the answer is a model estimate, not table-backed — the same
honesty pattern already used elsewhere when there's no real data to check
against.

---

## 5. Data shapes (what the pieces look like)

```python
class RiskCategory(str, Enum):
    VISA = "visa"
    HEALTH = "health"
    SEASONAL_WEATHER = "seasonal_weather"
    LOCAL_EVENT = "local_event"
    SAFETY = "safety"
    DISRUPTION = "disruption"

class RiskItem(BaseModel):
    risk_id: str
    category: RiskCategory
    severity: Literal["low", "medium", "high"]
    likelihood: Literal["low", "medium", "high"]
    summary: str
    mitigation: str | None = None
    source: str          # e.g. "synthetic reference data — illustrative only"

class RiskAgentResponse(BaseModel):
    rationale: str                    # the plain-language explanation
    highlighted_risk_ids: list[str]   # which RiskItems it actually used
    escalate: bool = False
    escalation_reason: str | None = None
    confidence: float
```

**One thing this design flags but does not decide on its own:** today, a
high-severity risk is marked by the model writing the literal word
`ESCALATE:` at the start of a sentence — there's no real field for it, so
whether the Orchestrator actually notices and surfaces it depends entirely on
the model preserving that exact word through its own summarization. (The
Accessibility agent has the identical problem with its own `VETO:` word.)
`RiskAgentResponse.escalate` above is written ready to fix this, but actually
wiring it in means changing the shared `AgentFinding` schema that Accessibility
and the Orchestrator also use — not something to change unilaterally inside
this one agent's folder. It's listed as a decision for the team in §7.

---

## 6. Leaving room to plug in real data later, without a rebuild

The three tables are read through one interface (`RiskDataProvider`, in
`providers/`) rather than being hard-coded into step 2 directly:

```python
class RiskDataProvider(Protocol):
    def covers(self, request: RiskProposalRequest) -> bool: ...
    def fetch(self, request: RiskProposalRequest) -> RiskFetchResult: ...
```

v1 has exactly one implementation of this, reading the three local tables.
The point of having the interface at all is that step 2 never talks to that
implementation directly — it only knows the interface. So if a real,
internet-connected data source is added later, it plugs in behind the same
interface, and steps 2–5 don't need to change at all. Whether and when to
build that live version is a separate decision, covered next.

---

## 7. What's deliberately left for later

### 7.1 Real, live data instead of the local tables

This design uses local tables on purpose, matching what the team's own
architecture notes already say for this agent ("visa reference data, seasonal
risk data, local events data — all explicitly synthetic and illustrative";
live regulatory/weather feeds are listed as out of scope). Swapping in a real
source later is possible (§6's interface exists for exactly this), but it's a
bigger and riskier change than it sounds, for three reasons worth knowing
before anyone decides to do it:

1. **Visa/entry** — there isn't one trustworthy place to search; each
   country publishes its own rules on its own official site. Even "live"
   lookup would need a table mapping each country to its official domain —
   which is itself local, hand-maintained data. Live retrieval doesn't remove
   that dependency, it just moves it.
2. **Seasonal/weather** — this one is realistic to search live; national
   weather agencies are a stable, enumerable set of trustworthy sources.
3. **Local events** — this is the hard one. There's no small set of official
   sources the way there is for weather. A live search here would either be
   wide open (and pull in low-quality or fake pages) or need a long,
   constantly-out-of-date list of per-city sources.

And whenever live search is added, one thing is not optional: every piece of
text it retrieves from the internet has to be scanned for injected
instructions before a model ever reads it — a malicious or compromised web
page telling the model what to say is a real, known attack, and nothing in
today's design defends against it because today's design has nothing to
defend against yet (the three tables are written by the team, not fetched).

### 7.2 A faster, fully offline safety check before the request even gets here

Separate from this agent specifically — this came up while looking at the
system's existing safety checks in general. Right now, catching a malicious
or manipulative request either uses a cheap pattern-matching check (which
misses a lot) or calls out to a model API (which is accurate but adds real
delay and depends on that API being reachable). A small classifier that runs
locally, with no network call, sitting in between the two, could catch the
obvious cases immediately and only bother the API for the unclear ones —
faster on average, and it keeps working even if the API is down. This is a
real option worth evaluating, not something this design commits to.

---

## 8. Decisions the team still needs to make

1. Add `escalate`/`escalation_reason` as real fields on the shared
   `AgentFinding` (§5) — affects Accessibility and the Orchestrator too, not
   just this agent.
2. Whether/when to build the live-data version (§7.1) — and if so, update the
   architecture notes to say so, since they currently say the opposite.
3. What actually goes in the three reference tables — this design fixes the
   shape and the lookup logic; the real rows (which countries, which
   seasons, which sample events) still need to be written.
4. Whether to build the offline pre-check (§7.2) — timing only, not a
   blocker for anything above.
