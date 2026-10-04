"""Non-agent LLM calls appear as Application consumption in admin."""

from types import SimpleNamespace
from uuid import uuid4

from flaskapp.database import (
    add_application_token_usage,
    ensure_planning_job,
    get_admin_token_summary,
    initialize,
)
from flaskapp.travel_ai.agents.orchestrator_agent.intake import extract_intent
from flaskapp.travel_ai.agents.orchestrator_agent.intake_schemas import (
    ExtractedIntent,
    IntakeExtraction,
)
from flaskapp.travel_ai.guardrails import cache
from flaskapp.travel_ai.guardrails.classifier import LlmGuardrail
from flaskapp.travel_ai.guardrails.schema import GuardrailVerdict
from flaskapp.travel_ai.usage import TokenUsageCallback


USAGE = {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50}


def _report_usage(config):
    response = SimpleNamespace(generations=[[SimpleNamespace(
        message=SimpleNamespace(usage_metadata=dict(USAGE))
    )]])
    for callback in (config or {}).get("callbacks") or []:
        callback.on_llm_end(response)


class PaidStructured:
    def __init__(self, response):
        self.response = response

    def invoke(self, _messages, config=None):
        _report_usage(config)
        return self.response


class PaidLlm:
    def __init__(self, response):
        self.response = response

    def with_structured_output(self, _schema, **_kwargs):
        return PaidStructured(self.response)


def test_application_usage_is_additive_and_visible_in_admin(tmp_path):
    database = tmp_path / "application-usage.sqlite3"
    initialize(database)
    request_id = str(uuid4())
    ensure_planning_job(database, request_id, None, {})

    add_application_token_usage(database, request_id, USAGE)
    add_application_token_usage(database, request_id, USAGE)

    summary = get_admin_token_summary(database)
    application = next(
        row for row in summary["agent_totals"] if row["agent"] == "Application"
    )
    assert application == {
        "agent": "Application",
        "input_tokens": 80,
        "output_tokens": 20,
        "total_tokens": 100,
    }


def test_intake_extraction_reports_application_tokens():
    usage = TokenUsageCallback()
    response = IntakeExtraction(
        question="Where are you travelling from?",
        intent=ExtractedIntent(destination="Tokyo"),
    )

    result = extract_intent(
        PaidLlm(response), "Tokyo", callbacks=[usage]
    )

    assert result == response
    assert usage.as_dict() == USAGE


def test_guardrail_classifier_reports_application_tokens():
    cache.clear()
    usage = TokenUsageCallback()
    response = GuardrailVerdict(
        decision="allow", category="none", confidence=0.99, rationale="safe"
    )
    guardrail = LlmGuardrail(None, llm=PaidLlm(response), callbacks=[usage])

    guardrail.screen_input(["quiet hotel near public transport"])

    assert usage.as_dict() == USAGE
