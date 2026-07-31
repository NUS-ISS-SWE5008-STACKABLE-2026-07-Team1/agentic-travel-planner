"""Application service coordinating validation, graph execution and assurance."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from langchain_openai import AzureChatOpenAI

from flaskapp.travel_ai.graph import SPECIALISTS, build_travel_graph
from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.safeguards import assess_plan
from flaskapp.travel_ai.schemas import PlanResponse, TravelRequest
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.terminal import log_payload

class TravelPlanningService:
    def __init__(self, *, api_key: str, endpoint: str, deployment: str, api_version: str,
                 temperature: float | None, timeout: float, trace_dir: Path,
                 database_path: Path | None = None, user_id: int | None = None,
                 cancel_event=None):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = deployment
        self.api_version = api_version
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
        client_options = {
            "api_key": self.api_key,
            "azure_endpoint": self.endpoint,
            "azure_deployment": self.model,
            "api_version": self.api_version,
            "timeout": self.timeout,
            "max_retries": 2,
        }
        if self.temperature is not None:
            client_options["temperature"] = self.temperature
        llm = AzureChatOpenAI(**client_options)
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
