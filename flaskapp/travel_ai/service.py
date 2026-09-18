"""Application service coordinating validation, graph execution and assurance."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from dataclasses import asdict

from flaskapp.travel_ai.dispatch import specialists_for
from flaskapp.travel_ai.recommendation import recommend_package
from flaskapp.travel_ai.sections import plan_packages, plan_sections
from flaskapp.travel_ai.graph import build_travel_graph
from flaskapp.travel_ai.guardrails import LlmGuardrail
from flaskapp.travel_ai.llm import build_llm
from flaskapp.travel_ai.a2a import request_message
from flaskapp.travel_ai.safeguards import assess_plan, disclose_unconsulted
from flaskapp.travel_ai.schemas import PlanPackage, PlanResponse, PlanSection, TravelRequest
from flaskapp.travel_ai.tracing import AuditTracer
from flaskapp.travel_ai.terminal import log_payload


def merge_accessibility_sources(plan, findings) -> None:
    """Guarantee vetted Accessibility Agent evidence is visible to the user."""
    accessibility_sources = [
        url
        for finding in findings if finding.agent == "accessibility_agent"
        for option in finding.options
        for url in option.source_urls
    ]
    plan.sources = list(dict.fromkeys([*plan.sources, *accessibility_sources]))


class TravelPlanningService:
    def __init__(self, *, provider: str, api_key: str, model: str,
                 temperature: float | None, timeout: float, trace_dir: Path,
                 endpoint: str | None = None, api_version: str | None = None,
                 base_url: str | None = None,
                 database_path: Path | str | None = None, user_id: int | None = None,
                 cancel_event=None, guardrail_settings: dict | None = None,
                 input_guardrail: dict | None = None):
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
        # The input gate already ran at the HTTP boundary; its verdict is
        # carried here only so the trace shows it. The output gate is
        # constructed from the same settings so both ends of the request agree
        # about which model and threshold are in force.
        self.guardrail_settings = guardrail_settings
        self.input_guardrail = input_guardrail

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
        if self.input_guardrail:
            tracer.record("guardrail_llm_verdict", "system", {
                "gate": "input", **self.input_guardrail,
            })
        llm = build_llm(
            provider=self.provider, api_key=self.api_key, model=self.model,
            temperature=self.temperature, timeout=self.timeout, endpoint=self.endpoint,
            api_version=self.api_version, base_url=self.base_url,
        )
        guardrail = (
            LlmGuardrail.from_settings(self.guardrail_settings)
            if self.guardrail_settings else None
        )
        # ONE decision, used twice. `make_specialist_node` raises
        # `Missing A2A request for {name}` when a node runs without an envelope
        # addressed to it, so a graph and a message list built from different
        # values is a crash rather than a degraded plan. Deriving both from the
        # same tuple is what makes that impossible.
        selected = specialists_for(request)
        tracer.record("specialists_dispatched", "orchestrator_agent", {
            "plan_scope": request.plan_scope,
            "dispatched": list(selected),
            "count": len(selected),
        })
        graph = build_travel_graph(
            llm, tracer, self.cancel_event, guardrail, specialists=selected
        )
        messages = [
            request_message(
                correlation_id=correlation_id,
                sender="orchestrator_agent",
                recipient=name,
                payload_type="TravelRequest",
                payload=request,
            )
            for name in selected
        ]
        result = graph.invoke({
            "request_id": request_id,
            "request": request.model_dump(mode="json"),
            "findings": [],
            "messages": messages,
        })
        findings = result["findings"]
        plan = result["plan"]
        # The orchestrator is asked to preserve sources, but accessibility
        # evidence must not depend on generative compliance. Copy its vetted
        # URLs into the user-visible plan deterministically.
        merge_accessibility_sources(plan, findings)
        # Stated, not left to be inferred from absence: a plan that never
        # mentions flights reads the same whether none were found or none were
        # sought, and only one of those is a reason to look elsewhere.
        disclose_unconsulted(plan, selected)
        plan.safety = assess_plan(request, plan, findings)
        tracer.record("assurance_completed", "system", {
            "passed": plan.safety.passed,
            "warning_count": len(plan.safety.warnings),
        })
        response = PlanResponse(
            request_id=request_id,
            plan=plan,
            agent_findings=findings,
            # A projection of the findings above, not a second source of truth:
            # the Option objects are the same objects the specialists returned,
            # so a figure shown cannot drift from a figure found.
            sections=[
                PlanSection(**asdict(section)) for section in plan_sections(findings)
            ],
            packages=[
                PlanPackage(**asdict(package)) for package in plan_packages(findings)
            ],
            # Arithmetic over options the specialists already returned, so a
            # figure here cannot disagree with the card it came from.
            recommendation=recommend_package(request, findings),
            trace_url=f"/api/v1/traces/{request_id}",
        )
        log_payload(f"REQUEST {request_id} | FINAL RECOMMENDATION", response)
        if self.database_path:
            from flaskapp.database import save_plan
            save_plan(self.database_path, request, response, result.get("messages", []), self.user_id)
        return response
