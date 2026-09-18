"""Projecting specialist findings into ordered, per-agent plan sections.

Pure: findings in, sections out. No model, no graph, no browser — which is the
point, because ordering, grouping and omission are exactly the rules that fail
silently and this repo has no JS test harness to catch them downstream.
"""

from flaskapp.travel_ai.schemas import AgentFinding, Option
from flaskapp.travel_ai.sections import plan_sections


def finding(agent, options=(), summary="ran"):
    return AgentFinding(agent=agent, summary=summary, options=list(options), confidence=0.9)


def option(name, category=None):
    return Option(category=category, name=name, description=f"{name} description")


def titles(sections):
    return [section.title for section in sections]


def test_sections_follow_a_fixed_order_not_the_order_findings_arrive():
    """Specialists run in parallel and the reducer concatenates them as they
    finish, so arrival order is nondeterministic. A traveller must not see the
    sections shuffle between two runs of the same trip.
    """
    forward = plan_sections([
        finding("flight_agent", [option("SQ1", "flight")]),
        finding("hotel_transport_agent", [option("Hotel", "hotel")]),
        finding("accessibility_agent"),
        finding("risk_advisory_agent"),
    ])
    reversed_arrival = plan_sections([
        finding("risk_advisory_agent"),
        finding("accessibility_agent"),
        finding("hotel_transport_agent", [option("Hotel", "hotel")]),
        finding("flight_agent", [option("SQ1", "flight")]),
    ])
    assert titles(forward) == titles(reversed_arrival)


def test_risk_advisory_is_always_last():
    for findings in (
        [finding("risk_advisory_agent"), finding("flight_agent")],
        [finding("flight_agent"), finding("risk_advisory_agent")],
    ):
        assert titles(plan_sections(findings))[-1].startswith("Risk")


def test_a_specialist_that_did_not_run_produces_no_section():
    """The conditionality you get for free from selective dispatch."""
    sections = plan_sections([finding("hotel_transport_agent", [option("Hotel", "hotel")])])
    assert not any("Flight" in title for title in titles(sections))


def test_one_hotel_finding_splits_across_a_package_and_a_section():
    """The split survives the move to tier columns: hotels are banded into
    packages, transport stays an unbanded section, and neither takes the
    other's options."""
    from flaskapp.travel_ai.sections import plan_packages

    one = finding("hotel_transport_agent", [
        option("Grand Hotel", "hotel"),
        option("Airport Express", "transport"),
    ])
    sections = plan_sections([one])
    assert [s.title for s in sections] == ["Arrival and local transport"]
    assert [o.name for o in sections[0].options] == ["Airport Express"]

    packaged = [o.name for p in plan_packages([one]) for g in p.groups for o in g.options]
    assert packaged == ["Grand Hotel"]


def test_a_finding_with_no_options_still_renders_its_summary():
    """It ran and had something to say. Silence reads as never having been asked."""
    sections = plan_sections([finding("risk_advisory_agent", summary="Visa on arrival.")])
    assert len(sections) == 1
    assert sections[0].summary == "Visa on arrival."
    assert sections[0].options == []


def test_an_uncategorised_option_falls_into_its_agents_default_section():
    """Accessibility and advisory build options through the model, not a builder."""
    sections = plan_sections([finding("accessibility_agent", [option("Evidence")])])
    assert len(sections) == 1
    assert [o.name for o in sections[0].options] == ["Evidence"]


def test_no_findings_yields_no_sections():
    assert plan_sections([]) == []


def test_an_agent_whose_sections_are_all_categorised_still_shows_when_it_found_nothing():
    """The hotel agent's PATH2 fallback strips every option by design.

    Both of its sections are category-specific, so without this the whole
    finding would vanish — the agent ran, said why it found nothing, and the
    traveller would see no trace of it.
    """
    sections = plan_sections([finding(
        "hotel_transport_agent", [], summary="No verified inventory for this city.",
    )])
    assert len(sections) == 1
    assert sections[0].summary == "No verified inventory for this city."
    assert sections[0].options == []


def test_an_empty_split_is_dropped_when_the_agent_did_find_something():
    """Only transport was found, so an empty "Hotel details" must not appear."""
    sections = plan_sections([finding(
        "hotel_transport_agent", [option("Airport Express", "transport")],
    )])
    assert titles(sections) == ["Arrival and local transport"]


def test_the_removed_result_sections_are_gone_from_the_renderer():
    """Three blocks were removed from the results view on request.

    Asserted against the source because this repo has no JS harness. The plan's
    own narrative and the aggregated source list are no longer rendered; what
    remains is title, summary, cost, alternatives and the per-specialist
    sections, whose cards carry each option's own references.
    """
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    assert 'textContent = "Suggested itinerary"' not in javascript
    assert 'appendListSection("Why this plan was recommended"' not in javascript
    assert 'appendListSection("Accessibility evidence and other sources"' not in javascript
    # What must survive: the evidence links now reach the traveller only through
    # the option cards, so that renderer is the thing holding the guarantee.
    assert 'sourceLabel.textContent = "Web references used"' in javascript
