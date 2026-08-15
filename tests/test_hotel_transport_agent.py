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

def test_search_hotels_uses_amadeus_when_configured(monkeypatch):
    monkeypatch.setenv("AMADEUS_API_KEY", "test-key")
    monkeypatch.setenv("AMADEUS_API_SECRET", "test-secret")
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    token_resp = MagicMock()
    token_resp.read.return_value = json.dumps({"access_token": "fake-token"}).encode()
    token_resp.__enter__ = MagicMock(return_value=token_resp)
    token_resp.__exit__ = MagicMock(return_value=False)

    hotel_resp = MagicMock()
    hotel_resp.read.return_value = json.dumps({
        "data": [
            {
                "hotel": {
                    "name": "Amadeus Hotel",
                    "amenities": [{"name": "wifi"}, {"name": "pool"}],
                },
            },
        ],
    }).encode()
    hotel_resp.__enter__ = MagicMock(return_value=hotel_resp)
    hotel_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [token_resp, hotel_resp]
        results = search_hotels({
            "destination": "Tokyo",
            "departure_date": "2026-10-10",
            "return_date": "2026-10-16",
            "travellers": 1,
            "budget": 3000,
            "currency": "USD",
        })

    assert len(results) == 1
    assert results[0]["name"] == "Amadeus Hotel"
    assert results[0]["source"] == "amadeus"
    assert "wifi" in results[0]["amenities"]
    assert "pool" in results[0]["amenities"]
    assert results[0]["limitations"] == ["Test API only; verify with production endpoint"]

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


def test_create_node_returns_callable():
    mock_llm = MagicMock()
    mock_tracer = MagicMock()
    node = create_node(mock_llm, mock_tracer)
    assert callable(node)


def test_create_node_specialist_raises_when_no_incoming_message():
    mock_llm = MagicMock()
    mock_tracer = MagicMock()
    node = create_node(mock_llm, mock_tracer)

    with pytest.raises(ValueError, match="Missing A2A request"):
        node({
            "request_id": "test-id",
            "request": {},
            "findings": [],
            "messages": [],
        })
