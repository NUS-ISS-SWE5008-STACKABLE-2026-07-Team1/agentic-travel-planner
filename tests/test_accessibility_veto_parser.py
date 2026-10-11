"""parse_veto replaced a backtracking regex; these pin the shapes it accepts."""

import pytest

from flaskapp.travel_ai.agents.accessibility_agent.integration import parse_veto


@pytest.mark.parametrize("warning, option", [
    ("VETO: Hotel Sakura — no step-free entrance", "Hotel Sakura"),
    ("veto: SQ 638 - no wheelchair assistance", "SQ 638"),
    ("VETO: Hotel-X - lift out of service", "Hotel-X"),
    ("VETO:\n Hotel A\n - no lift", "Hotel A"),
    ("  VETO: Ryokan — stairs only  ", "Ryokan"),
])
def test_named_option_is_extracted(warning, option):
    assert parse_veto(warning) == option


@pytest.mark.parametrize("warning", [
    "Not a veto — just a warning",
    "VETO: no separator here",
    "VETO: Hotel A -",
    "VETO: - reason with no option",
    "VETO: Hotel\nA - option split across lines",
    "",
])
def test_anything_else_is_not_a_veto(warning):
    assert parse_veto(warning) is None
