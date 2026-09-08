"""Tests for the system-prompt-leak canary token."""

from flaskapp.travel_ai.guardrails.canary import embed_canary, generate_canary, leaked


def test_generate_canary_is_unique_each_call():
    assert generate_canary() != generate_canary()


def test_embed_canary_includes_the_token():
    token = generate_canary()
    prompt = embed_canary("You are a travel planner.", token)
    assert token in prompt


def test_leaked_true_when_token_present_in_output():
    token = generate_canary()
    assert leaked(f"Sure, here it is: {token}", token) is True


def test_leaked_false_when_token_absent():
    token = generate_canary()
    assert leaked("Here is your travel plan for Tokyo.", token) is False


def test_leaked_false_on_non_string_output():
    token = generate_canary()
    assert leaked(None, token) is False
    assert leaked("", token) is False
