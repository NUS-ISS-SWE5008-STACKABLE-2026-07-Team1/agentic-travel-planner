"""Application service coordinating validation, graph execution and assurance."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from flaskapp.travel_ai.graph import SPECIALISTS, build_travel_graph
from flaskapp.travel_ai.llm import build_llm
from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.safeguards import assess_plan
from flaskapp.travel_ai.schemas import PlanResponse, TravelRequest
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.terminal import log_payload

class TravelPlanningService:
    def __init__(self, *, provider: str, api_key: str, model: str,
                 temperature: float | None, timeout: float, trace_dir: Path,
                 endpoint: str | None = None, api_version: str | None = None,
                 base_url: str | None = None,
                 database_path: Path | str | None = None, user_id: int | None = None,
                 cancel_event=None):
        self.api_key = api_key
        self.provider = provider
        self.endpoint = endpoint
        self.model = model
        self.api_version = api_version
        self.base_url = base_url
        self.temperature = temperature
        self.timeout = timeout
        self.trace_dir = trace_dir
        self.database_path = database_path
        self.user_id = user_id
        self.cancel_event = cancel_event

    def create_plan(self, request: TravelRequest, request_id: str | None = None) -> PlanResponse:
        correlation_id = uuid4() if request_id is None else UUID(request_id)
        request_id = str(correlation_id)
        log_payload(f"REQUEST {request_id} | USER FORM SUBMISSION", request)
        tracer = AuditTracer(self.trace_dir, request_id, self.database_path)
        tracer.record("request_accepted", "system", {
            "model": self.model,
            "has_accessibility_needs": bool(request.accessibility_needs),
            "preference_count": len(request.preferences),
        })
        llm = build_llm(
            provider=self.provider, api_key=self.api_key, model=self.model,
            temperature=self.temperature, timeout=self.timeout, endpoint=self.endpoint,
            api_version=self.api_version, base_url=self.base_url,
        )
        graph = build_travel_graph(llm, tracer, self.cancel_event)
        messages = [
            request_message(
                correlation_id=correlation_id,
                sender="orchestrator_agent",
                recipient=name,
                payload_type="TravelRequest",
                payload=request,
            )
            for name in SPECIALISTS
        ]
        result = graph.invoke({
            "request_id": request_id,
            "request": request.model_dump(mode="json"),
            "findings": [],
            "messages": messages,
        })
        findings = result["findings"]
        plan = result["plan"]
        plan.safety = assess_plan(request, plan, findings)
        tracer.record("assurance_completed", "system", {
            "passed": plan.safety.passed,
            "warning_count": len(plan.safety.warnings),
        })
        response = PlanResponse(
            request_id=request_id,
            plan=plan,
            agent_findings=findings,
            trace_url=f"/api/v1/traces/{request_id}",
        )
        log_payload(f"REQUEST {request_id} | FINAL RECOMMENDATION", response)
        if self.database_path:
            from flaskapp.database import save_plan
            save_plan(self.database_path, request, response, result.get("messages", []), self.user_id)
        return response
