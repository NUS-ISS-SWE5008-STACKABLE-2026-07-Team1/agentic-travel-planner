"""Focused unit tests for the hotel & transport agent provider functions and node factory."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from flaskapp.travel_ai.agents.hotel_transport_agent.agent import (
    create_node,
    search_hotels,
    search_transport,
)
from flaskapp.travel_ai.schemas import AgentFinding, Option

def test_search_hotels_returns_estimated_fallback_when_no_keys(monkeypatch):
    monkeypatch.delenv("AMADEUS_API_KEY", raising=False)
    monkeypatch.delenv("AMADEUS_API_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    results = search_hotels({
        "destination": "Tokyo",
        "departure_date": "2026-10-10",
        "return_date": "2026-10-16",
        "travellers": 1,
        "budget": 3000,
        "currency": "USD",
    })

    assert len(results) == 1
    assert results[0]["source"] == "estimated"
    assert "Sample Hotel in Tokyo" in results[0]["name"]
    assert results[0]["assumptions"]
    assert results[0]["limitations"]

def test_search_hotels_uses_budget_for_estimated_cost(monkeypatch):
    monkeypatch.delenv("AMADEUS_API_KEY", raising=False)
    monkeypatch.delenv("AMADEUS_API_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    results = search_hotels({
        "destination": "Tokyo",
        "departure_date": "2026-10-10",
        "return_date": "2026-10-16",
        "travellers": 1,
        "budget": 1200,
        "currency": "USD",
    })

    # One night (check-in != check-out), so nightly = 1200
    assert results[0]["estimated_cost_per_night"] == 1200.0

def test_search_transport_returns_estimated_fallback_when_no_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    results = search_transport({
        "destination": "Tokyo",
        "origin": "Singapore",
        "budget": 3000,
        "currency": "USD",
    })

    assert len(results) == 1
    assert results[0]["source"] == "estimated"
    assert results[0]["name"] == "Local transport estimate"

