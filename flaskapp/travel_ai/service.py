"""Application service coordinating validation, graph execution and assurance."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from langchain_openai import ChatOpenAI

from flaskapp.travel_ai.graph import SPECIALISTS, build_travel_graph
from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.safeguards import assess_plan
from flaskapp.travel_ai.schemas import PlanResponse, TravelRequest
from flaskapp.travel_ai.tracing import AuditTracer

class TravelPlanningService:
    def __init__(self, *, api_key: str, model: str, temperature: float, timeout: float, trace_dir: Path):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.trace_dir = trace_dir

    def create_plan(self, request: TravelRequest) -> PlanResponse:
        correlation_id = uuid4()
        request_id = str(correlation_id)
        tracer = AuditTracer(self.trace_dir, request_id)
        tracer.record("request_accepted", "system", {
            "model": self.model,
            "has_accessibility_needs": bool(request.accessibility_needs),
            "preference_count": len(request.preferences),
        })
        llm = ChatOpenAI(
            api_key=self.api_key,
            model=self.model,
            temperature=self.temperature,
            timeout=self.timeout,
            max_retries=2,
        )
        graph = build_travel_graph(llm, tracer)
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
        return PlanResponse(
            request_id=request_id,
            plan=plan,
            agent_findings=findings,
            trace_url=f"/api/v1/traces/{request_id}",
        )
