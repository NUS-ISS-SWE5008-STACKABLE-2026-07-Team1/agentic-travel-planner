from pathlib import Path

from flaskapp.travel_ai.schemas import AgentFinding, Option, TravelPlan
from flaskapp.travel_ai.service import merge_accessibility_sources


def test_accessibility_sources_are_guaranteed_in_user_visible_plan():
    plan = TravelPlan(
        title="Trip", summary="Summary", itinerary=[], rationale=[],
        sources=["https://existing.example/source"],
    )
    findings = [AgentFinding(
        agent="accessibility_agent", summary="Evidence",
        options=[Option(
            name="Step-free venue", description="Verified",
            source_urls=[
                "https://accessable.co.uk/venue",
                "https://existing.example/source",
            ],
        )], confidence=0.8,
    )]

    merge_accessibility_sources(plan, findings)

    assert plan.sources == [
        "https://existing.example/source",
        "https://accessable.co.uk/venue",
    ]


def test_chatbot_renders_sources_as_safe_clickable_links():
    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    # The aggregated `plan.sources` list is no longer rendered, so the option
    # cards are now the ONLY path by which vetted evidence reaches a traveller.
    # `merge_accessibility_sources` still populates plan.sources for the API and
    # the stored record, but nothing on screen depends on it any more.
    assert 'appendListSection("Accessibility evidence and other sources"' not in javascript
    # The evidence cards are no longer looked up by agent name in JS: they arrive
    # as one of the ordered sections `sections.plan_sections` builds, and the
    # heading is a Python constant asserted below rather than a string in here.
    assert 'section.options.forEach((option) => list.append(optionCard(option)))' in javascript
    assert '`Verify on ${url.hostname}`' in javascript
    assert 'sourceLabel.textContent = "Web references used"' in javascript
    assert 'accessibility-source-address' in javascript
    assert 'link.rel = "noopener noreferrer"' in javascript


def test_the_accessibility_evidence_section_is_named_and_ordered_server_side():
    """The heading moved out of JS, so it can finally be asserted directly.

    It must also precede the advisory section: evidence supports the plan, an
    advisory qualifies it.
    """
    from flaskapp.travel_ai.sections import SECTION_ORDER

    titles = [title for title, _agent, _category in SECTION_ORDER]
    assert "Accessibility evidence" in titles
    assert titles.index("Accessibility evidence") < titles.index("Risk and advisory")
