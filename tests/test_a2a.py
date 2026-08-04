from uuid import uuid4

import pytest
from pydantic import ValidationError

from flaskapp.travel_ai.a2a import A2AMessage, error_message, request_message, response_message
from flaskapp.travel_ai.schemas import AgentFinding, TravelRequest


def travel_request():
    return TravelRequest(
        origin="Singapore", destination="Tokyo", departure_date="2026-10-10",
        return_date="2026-10-16", travellers=1, traveller_ages=[30],
        traveller_genders=["prefer_not_to_say"], traveller_accessibility_needs=[[]],
        budget=3000,
    )


def test_request_and_response_share_correlation_but_not_message_id():
    request = request_message(
        correlation_id=uuid4(), sender="orchestrator_agent", recipient="flight_agent",
        payload_type="TravelRequest", payload=travel_request(),
    )
    finding = AgentFinding(agent="flight_agent", summary="Needs provider data", confidence=0.5)
    response = response_message(
        request=request, sender="flight_agent", payload_type="AgentFinding", payload=finding,
    )
    assert response.correlation_id == request.correlation_id
    assert response.message_id != request.message_id
    assert response.recipient == request.sender
    assert response.payload["agent"] == "flight_agent"


def test_error_has_machine_readable_failure_semantics():
    request = request_message(
        correlation_id=uuid4(), sender="orchestrator_agent", recipient="flight_agent",
        payload_type="TravelRequest", payload=travel_request(),
    )
    failure = error_message(
        request=request, sender="flight_agent", code="PROVIDER_TIMEOUT",
        message="Flight provider timed out", retryable=True,
    )
    assert failure.status == "failed"
    assert failure.error and failure.error.retryable


def test_contract_rejects_unknown_fields_and_invalid_error_state():
    base = {
        "correlation_id": str(uuid4()), "sender": "flight_agent",
        "recipient": "orchestrator_agent", "message_type": "response",
        "status": "completed", "payload_type": "AgentFinding", "payload": {},
    }
    with pytest.raises(ValidationError):
        A2AMessage.model_validate({**base, "unexpected": True})
    with pytest.raises(ValidationError):
        A2AMessage.model_validate({**base, "message_type": "error", "status": "failed"})


def test_response_rejects_wrong_agent():
    request = request_message(
        correlation_id=uuid4(), sender="orchestrator_agent", recipient="flight_agent",
        payload_type="TravelRequest", payload=travel_request(),
    )
    with pytest.raises(ValueError, match="response sender"):
        response_message(
            request=request, sender="accessibility_agent",
            payload_type="AgentFinding", payload={},
        )
