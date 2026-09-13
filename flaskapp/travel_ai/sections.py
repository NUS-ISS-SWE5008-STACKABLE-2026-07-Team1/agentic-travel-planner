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

from flaskapp.travel_ai.schemas import AgentFinding, Option

# (title, agent, category). A None category takes every option the agent
# returned that no other section for that agent already claimed — accessibility
# and risk build their options through the model rather than a builder, so
# theirs carry no category.
#
# Risk & advisory is last because an advisory qualifies the plan above it.
SECTION_ORDER: tuple[tuple[str, str, str | None], ...] = (
    ("Flight details", "flight_agent", None),
    ("Hotel details", "hotel_transport_agent", "hotel"),
    ("Arrival and local transport", "hotel_transport_agent", "transport"),
    ("Accessibility evidence", "accessibility_agent", None),
    ("Risk and advisory", "risk_advisory_agent", None),
)


@dataclass(frozen=True)
class PlanSection:
    """One specialist's contribution, as a traveller reads it."""

    title: str
    agent: str
    summary: str
    options: list[Option] = field(default_factory=list)


def _claimed_categories(agent: str) -> set[str]:
    """Categories another section already takes for this agent.

    Only meaningful for `hotel_transport_agent`, whose one finding splits in
    two. It stops an uncategorised option being repeated in both halves.
    """
    return {
        category for _title, section_agent, category in SECTION_ORDER
        if section_agent == agent and category is not None
    }


def plan_sections(findings: list[AgentFinding]) -> list[PlanSection]:
    """Ordered sections for the specialists that actually ran."""
    by_agent = {finding.agent: finding for finding in findings}
    sections: list[PlanSection] = []
    for title, agent, category in SECTION_ORDER:
        finding = by_agent.get(agent)
        if finding is None:
            continue
        if category is None:
            claimed = _claimed_categories(agent)
            options = [
                option for option in finding.options
                if option.category is None or option.category not in claimed
            ]
        else:
            options = [
                option for option in finding.options if option.category == category
            ]
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
        ))
    return sections
