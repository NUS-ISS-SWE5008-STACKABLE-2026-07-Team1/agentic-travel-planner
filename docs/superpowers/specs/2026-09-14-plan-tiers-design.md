# Price tiers and section icons

Status: designed
Date: 2026-09-14

## Problem

A plan's flight and hotel sections list every option the specialist returned in
the order it returned them. A traveller comparing five hotels between 900 and
4,680 SGD has to read each card and hold the numbers in their head; nothing in
the layout says which end of the range they are looking at.

The sections are also visually undifferentiated. Five headings in the same type
at the same weight, and the reader has to read each one to know whether they are
looking at flights or at a safety advisory.

## Scope

In scope: banding priced flight and hotel options into Luxury / Comfort /
Budget columns, keeping unpriced ones visible, and giving every section an icon.

Out of scope, explicitly:

- **Tiering Accessibility and Risk.** Four of 62 accessibility options and 29 of
  96 advisory options carry a price. Tier columns would put everything in one
  bucket and leave two empty headings on every plan.
- **Changing what the specialists return.** Tiers are a view over the options
  already found, exactly as the sections themselves are.
- **Currency conversion.** See "Decisions".

## Decisions

| Decision | Choice | Rejected alternative |
| --- | --- | --- |
| Where banding happens | `sections.py`, server-side | In `app.js` |
| Banding rule | Rank among priced options, cheapest to Budget | Against the stated budget; fixed thresholds |
| Unpriced options | A fourth group labelled "Not priced" | Filed under Budget; hidden |
| Mixed currencies | Skip banding for that section | Sort the raw numbers anyway |
| Empty tiers | Dropped | Rendered as an empty column |
| Contract | `tiers` added; `options` unchanged | `options` replaced by `tiers` |
| Icons | In `SECTION_ORDER`, server-side | Hard-coded in the renderer |

Four of these earn an explanation.

**Server-side, because the frontend cannot be tested.** This repo has no JS test
harness and no browser driver. An off-by-one in a tercile split does not crash —
it produces a plausible-looking wrong answer, which is precisely the failure a
test catches and an eye does not. Ordering and omission already live here for
the same reason.

**Rank, not absolute price.** Bands computed from the options actually returned
work in any currency and for any destination. The cost is that "Luxury" is
relative: a real flight finding spans 2,400–2,800 SGD, and calling the top of
that range Luxury claims a class distinction a 17% spread does not support. The
column captions therefore say the tiers are relative to this search, rather than
leaving the label to imply more than it means.

**Unpriced options are the majority of flight options** — 38 of 89 are priced —
and they come from the prompt-only fallback, which has no verified inventory to
cost against. Filing them under Budget would tell a traveller a flight is cheap
when nobody costed it. They appear in their own group, named for what is true
about them.

**Mixed currencies skip banding.** No finding currently mixes them, but sorting
raw numbers across currencies would rank 100 USD below 500 JPY. The guard costs
one comparison and prevents a wrong answer that would look right.

## Flow

```
finding.options
     │
     ▼
band_by_price(options)
     │
     ├─ more than one currency among priced? ──► one unlabelled tier (no banding)
     │
     ├─ priced options ─► sort ascending ─► split in three
     │                      cheapest third  ──► Budget
     │                      middle third    ──► Comfort
     │                      dearest third   ──► Luxury
     │                      (an empty tier is dropped, not rendered)
     │
     └─ unpriced options ──────────────────► "Not priced"
     │
     ▼
PlanSection.tiers            PlanSection.options unchanged
     │
     ▼
app.js: any tier carries a label ─► grid of the tiers present
        the one tier is unlabelled ─► the list it renders today
```

## Components

### `flaskapp/travel_ai/sections.py`

```python
TIER_LABELS = ("Budget", "Comfort", "Luxury")   # cheapest first
UNPRICED_LABEL = "Not priced"

# (title, icon, agent, category)
SECTION_ORDER: tuple[tuple[str, str, str, str | None], ...] = (
    ("Flight details",              "✈️", "flight_agent",          None),
    ("Hotel details",               "🏨", "hotel_transport_agent", "hotel"),
    ("Arrival and local transport", "🚗", "hotel_transport_agent", "transport"),
    ("Accessibility evidence",      "♿", "accessibility_agent",   None),
    ("Risk and advisory",           "⚠️", "risk_advisory_agent",   None),
)

BANDED_SECTIONS = frozenset({"Flight details", "Hotel details"})

@dataclass(frozen=True)
class PlanTier:
    label: str          # "" for an unbanded section's single tier
    options: list[Option]

def band_by_price(options: list[Option]) -> list[PlanTier]
```

`band_by_price` is pure and separately testable from `plan_sections`, because
the splitting rule is the part with edge cases and the assembly is not.

The tercile split is by rank, not by value: with `n` priced options each tier
takes `n // 3`, and the remainder goes to the cheaper tiers first, so four
options split 2/1/1 rather than leaving a tier empty while another holds three.

### `flaskapp/travel_ai/schemas.py`

`PlanSection` gains `icon: str = ""` and `tiers: list[PlanTier] = []`.

`options` is deliberately left as it is. The A2A artifact, the existing tests
and any current consumer keep working untouched, and `tiers` is purely additive.
The two cannot drift: both are produced by one call from the same `Option`
objects.

### `flaskapp/static/js/app.js`

The rule is one line: **render a grid when any tier carries a label, and a plain
list otherwise.** So today's sections, which get one unlabelled tier, are
unchanged; a flight section holding only unpriced options renders a grid of one
column headed "Not priced", which is correct — the heading is the information.

The tiers render as a CSS grid — three columns, collapsing to
one below roughly 900px — reusing `optionCard` unchanged, with a caption stating
that the tiers are relative to the options this search returned.

The icon renders before the title with `aria-hidden="true"`, so a screen reader
announces "Flight details" rather than "airplane Flight details". That follows
the feedback buttons, which already carry their emoji that way.

## Error handling

| Condition | Behaviour |
| --- | --- |
| No priced options | One "Not priced" tier; no empty columns |
| One or two priced options | One or two tiers; empty ones dropped |
| Priced options in several currencies | Banding skipped; one unlabelled tier |
| Section is not flight or hotel | One unlabelled tier, rendered as today's list |
| No options at all | No tiers; the section still renders its summary |

## Known limitations

- **"Luxury" is relative to the search, not to the market.** Captioned, not
  solved. Banding against the traveller's stated budget would mean something
  stronger, and is the obvious follow-on.
- **Unpriced options cannot be compared.** They are shown, but a traveller
  cannot place them against the banded ones.
- **Icons are emoji.** They render differently across platforms and carry no
  brand. Inline SVG would be consistent everywhere and is a larger change.
- **Frontend rendering stays unverified.** The banding is tested; what the
  browser draws is not, for want of a JS harness.

## Testing

- `band_by_price`: 1 through 6 priced options, asserting both the split sizes
  and that Budget holds the cheapest.
- Empty tiers are dropped rather than rendered.
- All-unpriced yields exactly one "Not priced" tier.
- Mixed currencies fall back to a single unlabelled tier.
- Non-banded sections produce one unlabelled tier holding every option.
- Every entry in `SECTION_ORDER` carries a non-empty icon.
- `PlanSection.options` still holds every option for a banded section, so the
  flat contract is unchanged.
