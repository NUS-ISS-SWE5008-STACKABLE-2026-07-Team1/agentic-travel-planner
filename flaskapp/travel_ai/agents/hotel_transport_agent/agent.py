"""Hotel & Transport Agent development and tool-integration entry point."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from flaskapp.travel_ai.agents.shared import SYSTEM_POLICY
from flaskapp.travel_ai.agents.hotel_transport_agent.prompt import INSTRUCTION
from flaskapp.travel_ai.a2a import A2AMessage, response_message
from flaskapp.travel_ai.schemas import AgentFinding, Option
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.terminal import log_payload
from flaskapp.travel_ai.usage import TokenUsageCallback
from flaskapp.database import save_agent_run

NAME = "hotel_transport_agent"

def _compact(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _safe_get(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Minimal stdlib JSON GET helper (no extra dependencies)."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": str(exc), "status": exc.code}
    except Exception as exc:
        return {"error": str(exc)}


def _safe_post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    """Minimal stdlib form POST helper."""
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": str(exc), "status": exc.code}
    except Exception as exc:
        return {"error": str(exc)}

def search_hotels(request: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Search accommodation options.

    Recommended free-tier providers:
    - Amadeus Self-Service APIs (https://developers.amadeus.com)
    - Google Places Text Search (https://developers.google.com/maps/documentation/places)
    """
    destination = request.get("destination", "")
    check_in = str(request.get("departure_date", ""))
    check_out = str(request.get("return_date", ""))
    adults = request.get("travellers", 1)
    currency = request.get("currency", "USD")
    budget = request.get("budget")
    accessibility = request.get("accessibility_needs", [])

    amadeus_key = os.getenv("AMADEUS_API_KEY")
    amadeus_secret = os.getenv("AMADEUS_API_SECRET")
    google_key = os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_API_KEY")

    results: list[dict[str, Any]] = []

    if amadeus_key and amadeus_secret:
        token = _safe_post_form(
            "https://test.api.amadeus.com/v1/security/oauth2/token",
            {
                "grant_type": "client_credentials",
                "client_id": amadeus_key,
                "client_secret": amadeus_secret,
            },
        )
        access_token = token.get("access_token")
        if access_token:
            city = destination[:3].upper()
            url = (
                "https://test.api.amadeus.com/v2/shopping/hotel-offers"
                f"?cityCode={urllib.parse.quote(city)}"
                f"&checkInDate={check_in}&checkOutDate={check_out}"
                f"&adults={adults}&currency={currency}&radius=20&radiusUnit=KM"
            )
            data = _safe_get(url, headers={"Authorization": f"Bearer {access_token}"})
            for offer in data.get("data", [])[:5]:
                hotel = offer.get("hotel", {})
                results.append({
                    "name": hotel.get("name", "Unknown Hotel"),
                    "estimated_cost_per_night": None,
                    "currency": currency,
                    "room_type": "standard",
                    "amenities": [a.get("name", "") for a in hotel.get("amenities", [])[:8]],
                    "distance_to_center_km": None,
                    "source": "amadeus",
                    "source_urls": [url],
                    "assumptions": ["Price and availability require supplier verification"],
                    "limitations": ["Test API only; verify with production endpoint"],
                })

    if not results and google_key:
        query = f"hotels in {destination}"
        url = (
            "https://maps.googleapis.com/maps/api/place/textsearch/json"
            f"?query={urllib.parse.quote(query)}&key={google_key}"
        )
        data = _safe_get(url)
        for place in data.get("results", [])[:5]:
            results.append({
                "name": place.get("name", "Unknown Hotel"),
                "estimated_cost_per_night": None,
                "currency": currency,
                "room_type": "standard",
                "amenities": [],
                "distance_to_center_km": None,
                "source": "google_places",
                "source_urls": [url],
                "assumptions": [
                    "Place search does not return pricing; use for location/name only"
                ],
                "limitations": [
                    "Requires separate pricing API for rates",
                    "Results are ranked by relevance, not price",
                ],
            })

    if not results:
        nightly = budget / max(((check_out != check_in) and 1 or 1), 1) if budget else 100.0
        results.append({
            "name": f"Sample Hotel in {destination}",
            "estimated_cost_per_night": round(nightly, 2),
            "currency": currency,
            "room_type": "standard",
            "amenities": ["wifi"],
            "distance_to_center_km": 3.0,
            "check_in": check_in,
            "check_out": check_out,
            "source": "estimated",
            "source_urls": [],
            "assumptions": [
                "No provider API key configured.",
                "Cost is a rough estimate, not a verified fare.",
                "Replace with real provider data before any booking decision.",
            ],
            "limitations": [
                "Mock data only; never treat as real availability or pricing"
            ],
        })

    return results


def search_transport(
    request: dict[str, Any],
    flight_findings: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Search local transport / transfer options.

    Recommended free-tier providers:
    - Google Maps Directions API / Distance Matrix API
    - Google Places API (find nearby stations / airports)
    """
    destination = request.get("destination", "")
    origin = request.get("origin", "")
    currency = request.get("currency", "USD")
    budget = request.get("budget")
    accessibility = request.get("accessibility_needs", [])

    google_key = os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_API_KEY")
    results: list[dict[str, Any]] = []

    if google_key:
        url = (
            "https://maps.googleapis.com/maps/api/directions/json"
            f"?origin=airport&destination={urllib.parse.quote(destination)}"
            f"&mode=driving&key={google_key}"
        )
        data = _safe_get(url)
        for route in data.get("routes", [])[:3]:
            leg = route.get("legs", [{}])[0]
            dur = leg.get("duration", {}).get("value", 0) // 60
            dist = leg.get("distance", {}).get("value", 0) / 1000
            results.append({
                "name": "Airport transfer",
                "mode": "taxi_or_rideshare",
                "duration_minutes": dur,
                "distance_km": round(dist, 1),
                "estimated_cost": None,
                "currency": currency,
                "frequency": "on_demand",
                "source": "google_directions",
                "source_urls": [url],
                "assumptions": [
                    "Requires real airport IATA code for accurate routing",
                ],
                "limitations": [
                    "Estimate only; verify with local transport providers",
                ],
            })

    if not results:
        results.append({
            "name": "Local transport estimate",
            "mode": "mixed",
            "duration_minutes": 45,
            "distance_km": 15.0,
            "estimated_cost": round(budget * 0.15, 2) if budget else 50.0,
            "currency": currency,
            "frequency": "daily",
            "source": "estimated",
            "source_urls": [],
            "assumptions": [
                "No provider API key configured.",
                "Cost and duration are rough estimates.",
                "Replace with real provider data before any booking decision.",
            ],
            "limitations": [
                "Mock data only; never treat as real schedule or pricing",
            ],
        })

    return results

def create_node(llm: ChatOpenAI, tracer: AuditTracer):
    structured_llm = llm.with_structured_output(AgentFinding, method="json_schema")

    def specialist(state: dict[str, Any]) -> dict[str, Any]:
        incoming = next(
            (m for m in state.get("messages", [])
             if m.message_type == "request" and m.recipient == NAME),
            None,
        )
        if incoming is None:
            raise ValueError(f"Missing A2A request for {NAME}")

        tracer.record("agent_started", NAME)
        if tracer.database_path:
            save_agent_run(tracer.database_path, state["request_id"], NAME, "processing")

        request = state["request"]

        # Pull flight schedules from flight_agent findings (if available)
        flight_schedules: list[dict[str, Any]] = []
        for finding in state.get("findings", []):
            if finding.agent == "flight_agent":
                flight_schedules = [opt.model_dump() for opt in finding.options]
                break

        # Call provider tools
        hotels = search_hotels(request)
        transports = search_transport(request, flight_schedules)

        # Assemble trusted context for the LLM
        context_parts = [
            "Travel request:\n" + _compact(request),
            "\nVerified hotel options (sourced from provider APIs, estimates flagged):\n"
            + _compact(hotels),
            "\nVerified transport options (sourced from provider APIs, estimates flagged):\n"
            + _compact(transports),
        ]
        if flight_schedules:
            context_parts.insert(
                1,
                "\nFlight schedules from flight_agent (use to align check-in/out and transfers):\n"
                + _compact(flight_schedules),
            )

        messages = [
            SystemMessage(content=SYSTEM_POLICY + "\n" + INSTRUCTION),
            HumanMessage(content="\n".join(context_parts)),
        ]

        usage = TokenUsageCallback()
        try:
            finding: AgentFinding = structured_llm.invoke(
                messages, config={"callbacks": [usage]}
            )
            finding.agent = NAME
            log_payload(
                f"REQUEST {state['request_id']} | {NAME.upper()} RESPONSE",
                finding,
            )
            tracer.record("agent_completed", NAME, {
                "confidence": finding.confidence,
                "option_count": len(finding.options),
                "warning_count": len(finding.warnings),
                **usage.as_dict(),
            })
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path,
                    state["request_id"],
                    NAME,
                    "completed",
                    usage.as_dict(),
                    finding,
                )
            outgoing = response_message(
                request=incoming,
                sender=NAME,
                payload_type="AgentFinding",
                payload=finding,
            )
            return {"findings": [finding], "messages": [outgoing]}
        except Exception as exc:
            tracer.record("agent_failed", NAME, {"error_type": type(exc).__name__})
            if tracer.database_path:
                save_agent_run(
                    tracer.database_path,
                    state["request_id"],
                    NAME,
                    "failed",
                    usage.as_dict(),
                    error_type=type(exc).__name__,
                )
            raise

    return specialist
