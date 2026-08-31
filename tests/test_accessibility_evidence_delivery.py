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
    assert 'appendListSection("Accessibility evidence and other sources", plan.sources, true)' in javascript
    assert 'finding.agent === "accessibility_agent"' in javascript
    assert 'evidenceHeading.textContent = "Accessibility evidence"' in javascript
    assert '`Verify on ${url.hostname}`' in javascript
    assert 'sourceLabel.textContent = "Web references used"' in javascript
    assert 'accessibility-source-address' in javascript
    assert 'link.rel = "noopener noreferrer"' in javascript
