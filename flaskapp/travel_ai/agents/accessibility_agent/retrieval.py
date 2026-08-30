"""Bounded, source-grounded retrieval for the accessibility specialist."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flaskapp.travel_ai.agents.accessibility_agent.models import AccessibilityEvidence
from flaskapp.travel_ai.agents.accessibility_agent.planning import build_search_plan
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
MAX_ATTEMPTS = 2


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
    """Backward-compatible access to the first privacy-safe planned query."""
    return build_search_plan(state).queries[0]


def _source_type(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if any(host == item or host.endswith(f".{item}") for item in OFFICIAL_DOMAINS):
        return "official"
    if host == "wheelmap.org" or host.endswith(".wheelmap.org"):
        return "crowdsourced"
    if any(host == item or host.endswith(f".{item}") for item in PRIMARY_DOMAINS):
        return "specialist"
    return "unknown"


def _error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, (URLError, OSError)):
        return "network_error"
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError)):
        return "invalid_response"
    return "provider_error"


def _search(query: str, domains: tuple[str, ...], api_key: str, timeout: float) -> dict:
    body = json.dumps({
        "api_key": api_key,
        "query": query,
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
    last_error: Exception | None = None
    for _attempt in range(MAX_ATTEMPTS):
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed HTTPS endpoint
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload.get("results", []), list):
                raise ValueError("provider results must be a list")
            return payload
        except Exception as exc:  # provider boundary: converted to a typed status below
            last_error = exc
            if not isinstance(exc, (TimeoutError, URLError, OSError)):
                break
    assert last_error is not None
    raise last_error


def retrieve_accessibility_evidence(state: TravelGraphState) -> dict:
    """Execute a bounded plan and return typed, deduplicated evidence."""
    domains = _allowed_domains()
    plan = build_search_plan(state)
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return {
            "status": "unavailable",
            "error_code": "not_configured",
            "reason": "TAVILY_API_KEY is not configured",
            "approved_domains": list(domains),
            "search_plan": plan.model_dump(),
            "results": [],
        }
    timeout = float(os.getenv("ACCESSIBILITY_SEARCH_TIMEOUT_SECONDS", "12"))
    results: list[dict] = []
    seen_urls: set[str] = set()
    errors: list[dict[str, str]] = []
    for query in plan.queries:
        try:
            payload = _search(query, domains, api_key, timeout)
        except Exception as exc:
            errors.append({"query_scope": query, "error_code": _error_code(exc)})
            continue
        for item in payload.get("results", [])[:MAX_RESULTS]:
            url = str(item.get("url") or "")
            if url in seen_urls or not _allowed_url(url, domains):
                continue
            seen_urls.add(url)
            evidence = AccessibilityEvidence(
                evidence_id=f"E{len(results) + 1}",
                title=str(item.get("title") or "Untitled")[:200],
                url=url,
                excerpt=str(item.get("content") or "")[:MAX_EXCERPT_CHARS],
                relevance=round(min(1.0, max(0.0, float(item.get("score") or 0))), 3),
                source_type=_source_type(url), query_scope=query,
                published_or_updated_at=item.get("published_date"),
            )
            results.append(evidence.model_dump())
            if len(results) >= MAX_RESULTS:
                break
        if len(results) >= MAX_RESULTS:
            break
    status = "available" if results and not errors else "partial" if results else "unavailable" if errors else "no_results"
    return {
        "status": status,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "approved_domains": list(domains),
        "search_plan": plan.model_dump(),
        "errors": errors,
        "results": results,
    }
