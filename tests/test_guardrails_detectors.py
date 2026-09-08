"""Tests for the canonical shared bias/toxicity detectors."""

from flaskapp.travel_ai.guardrails.detectors import detect_bias, detect_toxicity


def test_bare_protected_attribute_is_medium_not_blocked():
    result = detect_bias("muslim travellers need a prayer room at the airport")
    assert result["risk_level"] == "medium"
    assert "religion" in result["attributes_detected"]


def test_attribute_plus_stereotype_trigger_is_high():
    result = detect_bias("Muslims should not be seated near exits")
    assert result["risk_level"] == "high"
    assert result["stereotype_signal"] is True


def test_no_protected_attribute_is_low():
    result = detect_bias("climb Kilimanjaro before sunrise")
    assert result["risk_level"] == "low"
    assert result["attributes_detected"] == []


def test_toxicity_word_boundary_avoids_kilimanjaro():
    result = detect_toxicity("we want to climb Kilimanjaro")
    assert result["flagged"] is False


def test_toxicity_word_boundary_avoids_diet():
    result = detect_toxicity("please suggest a diet-friendly restaurant")
    assert result["flagged"] is False


def test_toxicity_catches_whole_word_match():
    result = detect_toxicity("this planner is stupid and useless")
    assert result["flagged"] is True
    assert "toxicity" in result["labels"]


def test_toxicity_catches_multi_word_identity_attack():
    result = detect_toxicity("all muslims are the same")
    assert result["flagged"] is True
    assert "identity_attack" in result["labels"]
