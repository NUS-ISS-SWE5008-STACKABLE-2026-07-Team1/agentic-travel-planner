"""Banding priced options into Luxury / Comfort / Budget.

Pure: options in, tiers out. The split is where the edge cases are — an
off-by-one here does not crash, it produces a plausible-looking wrong answer,
and this repo has no JS harness downstream to catch it.
"""

from flaskapp.travel_ai.schemas import Option
from flaskapp.travel_ai.sections import UNPRICED_LABEL, band_by_price


def priced(name, cost, currency="SGD"):
    return Option(name=name, description="d", estimated_cost=cost, currency=currency)


def unpriced(name):
    return Option(name=name, description="d")


def labels(tiers):
    return [tier.label for tier in tiers]


def names(tiers, label):
    return [o.name for tier in tiers if tier.label == label for o in tier.options]


def test_the_cheapest_option_lands_in_budget():
    tiers = band_by_price([priced("c", 300), priced("a", 100), priced("b", 200)])
    assert names(tiers, "Budget") == ["a"]
    assert names(tiers, "Comfort") == ["b"]
    assert names(tiers, "Luxury") == ["c"]


def test_tiers_are_ordered_budget_first():
    tiers = band_by_price([priced("a", 100), priced("b", 200), priced("c", 300)])
    assert labels(tiers) == ["Budget", "Comfort", "Luxury"]


def test_a_remainder_goes_to_the_cheaper_tiers():
    """Four options split 2/1/1, not 1/1/1 with one left over."""
    tiers = band_by_price([priced(n, c) for n, c in
                           [("a", 100), ("b", 200), ("c", 300), ("d", 400)]])
    assert [len(t.options) for t in tiers] == [2, 1, 1]
    assert names(tiers, "Budget") == ["a", "b"]


def test_five_options_split_two_two_one():
    tiers = band_by_price([priced(n, c) for n, c in
                           [("a", 1), ("b", 2), ("c", 3), ("d", 4), ("e", 5)]])
    assert [len(t.options) for t in tiers] == [2, 2, 1]


def test_two_options_produce_two_tiers_not_three_with_a_gap():
    tiers = band_by_price([priced("a", 100), priced("b", 200)])
    assert len(tiers) == 2
    assert all(tier.options for tier in tiers)


def test_one_priced_option_produces_one_tier():
    assert len(band_by_price([priced("a", 100)])) == 1


def test_unpriced_options_get_their_own_group():
    """They are the majority of flight options, and nobody costed them."""
    tiers = band_by_price([priced("a", 100), unpriced("x"), unpriced("y")])
    assert UNPRICED_LABEL in labels(tiers)
    assert names(tiers, UNPRICED_LABEL) == ["x", "y"]


def test_the_unpriced_group_comes_last():
    tiers = band_by_price([unpriced("x"), priced("a", 100), priced("b", 200)])
    assert labels(tiers)[-1] == UNPRICED_LABEL


def test_all_unpriced_yields_only_that_group():
    tiers = band_by_price([unpriced("x"), unpriced("y")])
    assert labels(tiers) == [UNPRICED_LABEL]


def test_mixed_currencies_skip_banding_entirely():
    """Sorting raw numbers across currencies would rank 100 USD below 500 JPY."""
    tiers = band_by_price([priced("a", 100, "USD"), priced("b", 500, "JPY")])
    assert labels(tiers) == [""]
    assert len(tiers[0].options) == 2


def test_no_options_yields_no_tiers():
    assert band_by_price([]) == []


# --- sections carry icons and tiers ------------------------------------------

from flaskapp.travel_ai.schemas import AgentFinding  # noqa: E402
from flaskapp.travel_ai.sections import SECTION_ORDER, plan_sections  # noqa: E402


def finding(agent, options=(), summary="ran"):
    return AgentFinding(agent=agent, summary=summary, options=list(options), confidence=0.9)


def hotel(name, cost):
    return Option(category="hotel", name=name, description="d",
                  estimated_cost=cost, currency="SGD")


def test_every_section_carries_an_icon():
    """Asserted on the unpacked shape, not row[1] — with 3-tuples that silently
    picked up the agent name and passed."""
    for title, icon, agent, _category in SECTION_ORDER:
        assert icon.strip(), f"{title} has no icon"
        assert icon != agent and not icon.isascii(), f"{title}: {icon!r} is not an icon"


def test_hotels_are_banded_into_tier_columns():
    """Banded sections are served by `plan_packages`, not `plan_sections`."""
    from flaskapp.travel_ai.sections import plan_packages

    packages = plan_packages([finding("hotel_transport_agent", [
        hotel("cheap", 500), hotel("mid", 1500), hotel("dear", 4000),
    ])])
    assert [p.label for p in packages] == ["Budget", "Comfort", "Luxury"]
    assert all(g.icon.strip() for p in packages for g in p.groups)


def test_an_unbanded_section_gets_one_unlabelled_tier():
    """Accessibility and risk are essentially unpriced; columns would be noise."""
    sections = plan_sections([finding("risk_advisory_agent", [
        Option(name="visa", description="d"),
    ])])
    assert [t.label for t in sections[0].tiers] == [""]


def test_every_banded_option_reaches_exactly_one_column():
    """Nothing is lost or duplicated by the transpose."""
    from flaskapp.travel_ai.sections import plan_packages

    packages = plan_packages([finding("hotel_transport_agent", [
        hotel("cheap", 500), hotel("dear", 4000),
    ])])
    names = [o.name for p in packages for g in p.groups for o in g.options]
    assert sorted(names) == ["cheap", "dear"]


def test_the_renderer_uses_the_icon_and_the_tiers():
    """Asserted against the source: this repo has no JS harness."""
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    assert 'aria-hidden' in javascript, "an icon must not be announced as 'airplane'"
    assert "section.tiers" in javascript
    assert "plan-tier-grid" in javascript


def test_the_stylesheet_collapses_the_tier_grid_on_narrow_screens():
    from pathlib import Path

    css = Path("flaskapp/static/css/app.css").read_text(encoding="utf-8")
    assert ".plan-tier-grid" in css
    assert "@media" in css


def test_the_tier_palette_is_declared_as_one_set():
    """Three tints in one place, so they read as a scale rather than three
    unrelated colours picked at three different times."""
    from pathlib import Path

    css = Path("flaskapp/static/css/app.css").read_text(encoding="utf-8")
    for token in ("--tier-budget", "--tier-comfort", "--tier-luxury"):
        assert token in css
    assert ".plan-group-heading" in css
    assert ".flight-times" in css


def test_the_tier_grid_renders_before_the_remaining_sections():
    """Flights and hotels are the plan; accessibility and risk qualify it, so
    they belong underneath rather than above."""
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    grid = javascript.index('grid.className = "plan-tier-grid"')
    sections = javascript.index("(response.sections || []).forEach")
    assert grid < sections, "the tier grid must be appended before the sections loop"


def test_each_section_is_its_own_aligned_row_in_the_grid():
    """Rows are sections, columns are tiers, so Budget sits under Budget in
    both the flight row and the hotel row."""
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    assert "plan-tier-row-heading" in javascript
    assert "plan-tier-cell" in javascript
    css = Path("flaskapp/static/css/app.css").read_text(encoding="utf-8")
    assert ".plan-tier-row-heading" in css
    assert "grid-column: 1 / -1" in css


def test_the_flight_card_does_not_repeat_its_identity_or_run_words_together():
    """Reported: the card showed the name AND the reference, and rendered
    "07:04+1SIN" with nothing between the day marker and the airport."""
    from pathlib import Path

    javascript = Path("flaskapp/static/js/app.js").read_text(encoding="utf-8")
    # The airport code is spaced away from the day marker.
    assert 'flight-dest' in javascript
    css = Path("flaskapp/static/css/app.css").read_text(encoding="utf-8")
    dest = css[css.index(".flight-dest"):css.index(".flight-dest") + 200]
    assert "margin-left" in dest
    # A card with a schedule shows its identity once: the reference line, not
    # the raw option name as well.
    assert "option.schedule_display ? \"\" : option.name" in javascript
