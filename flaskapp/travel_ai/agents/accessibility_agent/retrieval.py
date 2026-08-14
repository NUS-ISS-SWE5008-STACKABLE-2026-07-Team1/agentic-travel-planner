"""Bounded, source-grounded retrieval for the accessibility specialist."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flaskapp.travel_ai.schemas import TravelGraphState

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
PRIMARY_DOMAINS = (
    "wheeltheworld.com", "accessiblego.com", "pantou.org", "accessable.co.uk",
    "wheelmap.org", "accessibility.cloud",
)
OFFICIAL_DOMAINS = (
    "mta.info", "tfl.gov.uk", "ratp.fr", "delta.com", "lufthansa.com",
    "emirates.com", "visitbritain.com", "japan.travel", "jnto.go.jp",
)
MAX_RESULTS = 8
MAX_EXCERPT_CHARS = 1_500


def _allowed_domains() -> tuple[str, ...]:
    extras = tuple(
        item.strip().lower().lstrip(".")
        for item in os.getenv("ACCESSIBILITY_EXTRA_DOMAINS", "").split(",")
        if item.strip()
    )
    return (*PRIMARY_DOMAINS, *OFFICIAL_DOMAINS, *extras)


def _allowed_url(url: str, domains: tuple[str, ...]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return urlparse(url).scheme == "https" and any(
        host == domain or host.endswith(f".{domain}") for domain in domains
    )


def _query(state: TravelGraphState) -> str:
    request = state.get("request", {})
    destination = str(request.get("destination") or "destination")[:100]
    return (
        f"{destination} verified accessibility measurements hotels attractions public transit "
        "airline special assistance step-free access door width bed height roll-in shower "
        "elevator accessible toilet"
    )


def retrieve_accessibility_evidence(state: TravelGraphState) -> dict:
    """Search only approved domains and return small excerpts for grounded synthesis."""
    domains = _allowed_domains()
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return {
            "status": "unavailable",
            "reason": "TAVILY_API_KEY is not configured",
            "approved_domains": list(domains),
            "results": [],
        }

    body = json.dumps({
        "api_key": api_key,
        "query": _query(state),
        "search_depth": "basic",
        "max_results": MAX_RESULTS,
        "include_answer": False,
        "include_raw_content": False,
        "include_domains": list(domains),
    }).encode("utf-8")
    request = Request(
        TAVILY_SEARCH_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "AtlasTravelPlanner/1.0"},
    )
    timeout = float(os.getenv("ACCESSIBILITY_SEARCH_TIMEOUT_SECONDS", "12"))
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed HTTPS endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "status": "unavailable",
            "reason": f"search failed: {type(exc).__name__}",
            "approved_domains": list(domains),
            "results": [],
        }

    results = []
    for item in payload.get("results", [])[:MAX_RESULTS]:
        url = str(item.get("url") or "")
        if not _allowed_url(url, domains):
            continue
        results.append({
            "title": str(item.get("title") or "Untitled")[:200],
            "url": url,
            "excerpt": str(item.get("content") or "")[:MAX_EXCERPT_CHARS],
            "relevance": round(float(item.get("score") or 0), 3),
        })
    return {
        "status": "available" if results else "no_results",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "approved_domains": list(domains),
        "results": results,
    }
