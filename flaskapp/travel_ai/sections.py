"""Per-specialist sections, projected from the findings a plan was built on.

The orchestrator's `itinerary` reasons ACROSS specialists — it sequences a
flight, a check-in and a transfer into a trip, and nothing else produces that.
What it cannot do is show which flight it means: its prose is a paraphrase of
findings, so a figure in it is the model's restatement rather than the
specialist's claim.

These sections are the other half. They are a PROJECTION of the findings, never
a regeneration: the `Option` objects here are the same objects the specialist
returned, so a price shown cannot drift from a price found. That is why the
orchestrator is not asked to fill structured fields instead.

Two properties worth stating, because both are easy to lose later:

Order is fixed by `SECTION_ORDER`, not by the order findings arrive. The
specialists run in parallel and `merge_findings` concatenates them as each
finishes, so arrival order is nondeterministic — without a fixed order a
traveller would see the sections shuffle between two runs of the same trip.

A section exists only when its finding exists. Selective dispatch therefore
needs no representation here: a hotel-only plan has no flight finding, so it has
no flight section, and this module contains no test of `plan_scope` at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import re
from datetime import datetime

from flaskapp.travel_ai.schemas import AgentFinding, Option, OptionSchedule

# (title, icon, agent, category). A None category takes every option the agent
# returned that no other section for that agent already claimed — accessibility
# and risk build their options through the model rather than a builder, so
# theirs carry no category.
#
# The icon lives here rather than in the renderer for the same reason the title
# does: server-side is where a test can reach it.
#
# Risk & advisory is last because an advisory qualifies the plan above it.
SECTION_ORDER: tuple[tuple[str, str, str, str | None], ...] = (
    ("Flight details", "✈️", "flight_agent", None),
    ("Hotel details", "🏨", "hotel_transport_agent", "hotel"),
    ("Arrival and local transport", "🚗", "hotel_transport_agent", "transport"),
    ("Accessibility evidence", "♿", "accessibility_agent", None),
    ("Risk and advisory", "⚠️", "risk_advisory_agent", None),
)

# Only these carry prices worth comparing. Accessibility and risk options are
# almost never costed, so tier columns would put everything in one bucket and
# leave two empty headings on every plan.
BANDED_SECTIONS = frozenset({"Flight details", "Hotel details"})


# Cheapest first. The labels are relative to the options a search returned, not
# to the market: a real flight finding spans 2,400-2,800 SGD, so "Luxury" here
# means "the dearest of these", which the rendered caption says out loud.
TIER_LABELS: tuple[str, str, str] = ("Budget", "Comfort", "Luxury")

# Named for what is true about them. Unpriced options are the MAJORITY of flight
# options — they come from the prompt-only fallback, which has no verified
# inventory to cost against — and filing them under Budget would tell a
# traveller a flight is cheap when nobody costed it.
UNPRICED_LABEL = "Not priced"

# A trailing -YYYYMMDD on a flight id, which the card shows separately.
_DATE_SUFFIX = re.compile(r"-\d{8}$")


@dataclass(frozen=True)
class PlanTier:
    """One price band within a section. An empty `label` means no banding."""

    label: str
    options: list[Option] = field(default_factory=list)


def band_by_price(options: list[Option]) -> list[PlanTier]:
    """Split options into price bands, cheapest first.

    By RANK, not by value: bands computed from what a search actually returned
    work in any currency and for any destination, where fixed thresholds do not.

    Banding is skipped when the priced options span more than one currency.
    Sorting raw numbers across currencies would rank 100 USD below 500 JPY — a
    wrong answer that looks entirely right. No finding currently mixes them; the
    guard costs one comparison.

    An empty band is dropped rather than rendered, so two priced options give two
    tiers and not three with a gap.
    """
    if not options:
        return []
    priced = [option for option in options if option.estimated_cost is not None]
    unpriced = [option for option in options if option.estimated_cost is None]

    currencies = {option.currency for option in priced if option.currency}
    if len(currencies) > 1:
        return [PlanTier(label="", options=list(options))]

    tiers: list[PlanTier] = []
    if priced:
        ranked = sorted(priced, key=lambda option: option.estimated_cost)
        # The remainder goes to the cheaper tiers, so four options split 2/1/1
        # rather than leaving a tier empty while another holds three.
        base, extra = divmod(len(ranked), len(TIER_LABELS))
        start = 0
        for position, label in enumerate(TIER_LABELS):
            size = base + (1 if position < extra else 0)
            chunk = ranked[start:start + size]
            start += size
            if chunk:
                tiers.append(PlanTier(label=label, options=chunk))
    if unpriced:
        tiers.append(PlanTier(label=UNPRICED_LABEL, options=unpriced))
    return tiers


@dataclass(frozen=True)
class PlanGroup:
    """One section's contribution to one price tier."""

    title: str
    icon: str
    options: list[Option] = field(default_factory=list)


@dataclass(frozen=True)
class PlanPackage:
    """One tier column."""

    label: str
    groups: list[PlanGroup] = field(default_factory=list)


@dataclass(frozen=True)
class PlanSection:
    """One specialist's contribution, as a traveller reads it."""

    title: str
    agent: str
    summary: str
    options: list[Option] = field(default_factory=list)
    icon: str = ""
    # A view over `options`, never a second source of truth: one call builds
    # both from the same objects, so they cannot disagree.
    tiers: list[PlanTier] = field(default_factory=list)


def _claimed_categories(agent: str) -> set[str]:
    """Categories another section already takes for this agent.

    Only meaningful for `hotel_transport_agent`, whose one finding splits in
    two. It stops an uncategorised option being repeated in both halves.
    """
    return {
        category for _title, _icon, section_agent, category in SECTION_ORDER
        if section_agent == agent and category is not None
    }


def format_schedule(schedule: OptionSchedule) -> dict[str, str]:
    """A grounded flight as an airline site would show it.

    Formatted here rather than in the renderer so the day-change marker is
    computed and asserted. A flight leaving 17:15 and arriving 01:06 is not a
    nineteen-hour mistake, and a card that drops the date change says it is.

    The wall clock is read as written and never converted. The two offsets
    differ because they are clocks at two different airports; normalising them
    to one zone would print times no boarding pass agrees with.

    Returns {} for anything unparseable — a malformed timestamp degrades one
    card, it does not fail a plan.
    """
    try:
        departs = datetime.fromisoformat(schedule.depart)
        arrives = datetime.fromisoformat(schedule.arrive)
    except (TypeError, ValueError):
        return {}
    days = (arrives.date() - departs.date()).days
    return {
        # `flight_id` embeds the departure date (TR4011-20261015) and the card
        # shows that date on its own line, so the suffix is noise here. Anything
        # that is not a trailing 8-digit date is left exactly as given.
        "reference": _DATE_SUFFIX.sub("", schedule.reference),
        "date": departs.strftime("%d %b %Y"),
        "depart": departs.strftime("%H:%M"),
        "arrive": arrives.strftime("%H:%M"),
        # Counted, not assumed to be at most one: a two-stop itinerary can
        # cross two dates.
        "day_offset": f"+{days}" if days > 0 else "",
        "dest_code": schedule.dest_code,
    }


def _options_for(finding: AgentFinding, agent: str, category: str | None) -> list[Option]:
    """The options one section claims from one finding.

    Shared by `plan_sections` and `plan_packages` so the two cannot disagree
    about which options belong to a section — a hotel option appearing in the
    Hotel section but not the Hotel tier column would be a silent, plausible
    kind of wrong.

    A None category takes everything no other section for this agent claims,
    which is how accessibility and risk get their uncategorised options without
    stealing the hotel agent's transport ones.
    """
    if category is None:
        claimed = _claimed_categories(agent)
        return [
            option for option in finding.options
            if option.category is None or option.category not in claimed
        ]
    return [option for option in finding.options if option.category == category]


def _displayed(option: Option) -> Option:
    """A copy of the option carrying its formatted schedule.

    A copy, because the same `Option` objects are handed to the traveller AND
    stored as `agent_findings`. Mutating one to add a display string would put a
    rendering concern into the record of what a specialist actually found.
    """
    if option.schedule is None:
        return option
    display = format_schedule(option.schedule)
    if not display:
        return option
    return option.model_copy(update={"schedule_display": display})


def plan_packages(findings: list[AgentFinding]) -> list[PlanPackage]:
    """The banded sections, transposed into tier-major columns.

    `plan_sections` is section-major — a Flight section holding tiers. A column
    is the other way round, so a traveller reads a trip rather than pairing two
    grids in their head.

    Banding stays INDEPENDENT per section: six flights split 2/2/2 while three
    hotels split 1/1/1, and a column shows what each contributed. Ranking them
    together would be meaningless — a flight and a hotel are not comparable on
    price, and "the third cheapest thing" is not a tier.

    A section that contributed nothing to a tier is simply absent from that
    column, which is also how a hotel-only plan renders: no flight finding, no
    flight group, and no special case for scope anywhere in here.
    """
    by_agent = {finding.agent: finding for finding in findings}
    banded: list[tuple[str, str, list[PlanTier]]] = []
    for title, icon, agent, category in SECTION_ORDER:
        if title not in BANDED_SECTIONS:
            continue
        finding = by_agent.get(agent)
        if finding is None:
            continue
        options = _options_for(finding, agent, category)
        if options:
            banded.append((title, icon, band_by_price(options)))

    packages: list[PlanPackage] = []
    for label in (*TIER_LABELS, UNPRICED_LABEL):
        groups = [
            PlanGroup(title=title, icon=icon, options=[_displayed(o) for o in tier.options])
            for title, icon, tiers in banded
            for tier in tiers
            if tier.label == label and tier.options
        ]
        if groups:
            packages.append(PlanPackage(label=label, groups=groups))
    return packages


def _tiers_for(title: str, options: list[Option]) -> list[PlanTier]:
    """Bands for a banded section, one unlabelled tier for everything else.

    No options means no tiers on BOTH paths. They used to disagree: the banded
    path returned nothing while the unbanded one returned a single empty tier,
    so an agent that ran and found nothing rendered an empty column heading in
    one section and not in another.
    """
    if not options:
        return []
    if title in BANDED_SECTIONS:
        return band_by_price(options)
    return [PlanTier(label="", options=options)]


def plan_sections(findings: list[AgentFinding]) -> list[PlanSection]:
    """Ordered sections for the specialists that actually ran."""
    by_agent = {finding.agent: finding for finding in findings}
    sections: list[PlanSection] = []
    for title, icon, agent, category in SECTION_ORDER:
        # Banded sections are served as tier columns by `plan_packages`.
        # Emitting them here too would put the same options on screen twice.
        if title in BANDED_SECTIONS:
            continue
        finding = by_agent.get(agent)
        if finding is None:
            continue
        options = _options_for(finding, agent, category)
        # A split section with nothing in it is dropped — an agent whose finding
        # holds no transport options has not said anything about transport.
        #
        # Unless the agent found NOTHING AT ALL. Both of the hotel agent's
        # sections are category-specific, and its PATH2 fallback strips every
        # option by design, so dropping empties unconditionally would erase a
        # finding whose summary is the only thing explaining why nothing was
        # found. In that case its first section carries the summary, matching
        # what a whole-agent section does.
        if category is not None and not options and finding.options:
            continue
        if not options and any(section.agent == agent for section in sections):
            continue
        sections.append(PlanSection(
            title=title, agent=agent, summary=finding.summary, options=options,
            icon=icon,
            tiers=_tiers_for(title, options),
        ))
    return sections
