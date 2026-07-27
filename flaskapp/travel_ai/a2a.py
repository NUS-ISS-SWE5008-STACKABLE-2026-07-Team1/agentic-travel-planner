"""Versioned agent-to-agent (A2A) communication contract.

All inter-agent messages must be created through ``A2AMessage`` (or the helper
constructors below) so malformed hand-offs fail before reaching another agent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

A2A_PROTOCOL_VERSION = "1.0"

AgentId = Literal[
    "system",
    "orchestrator_agent",
    "flight_agent",
    "hotel_transport_agent",
    "accessibility_agent",
    "risk_advisory_agent",
]
MessageType = Literal["request", "response", "event", "error"]
MessageStatus = Literal["pending", "completed", "failed"]


class A2AError(BaseModel):
    """Machine-readable failure returned instead of an agent result."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class A2AMessage(BaseModel):
    """Transport-neutral envelope shared by every agent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    protocol_version: Literal["1.0"] = A2A_PROTOCOL_VERSION
    message_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID
    sender: AgentId
    recipient: AgentId
    message_type: MessageType
    status: MessageStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    error: A2AError | None = None

    @model_validator(mode="after")
    def validate_semantics(self) -> "A2AMessage":
        if self.sender == self.recipient:
            raise ValueError("sender and recipient must differ")
        if self.message_type == "error":
            if self.status != "failed" or self.error is None:
                raise ValueError("error messages require status='failed' and error details")
        elif self.error is not None or self.status == "failed":
            raise ValueError("failed status/error details require message_type='error'")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return self


def request_message(*, correlation_id: UUID, sender: AgentId, recipient: AgentId,
                    payload_type: str, payload: BaseModel | dict[str, Any]) -> A2AMessage:
    """Create a pending request envelope from a typed model or JSON-ready dict."""
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return A2AMessage(correlation_id=correlation_id, sender=sender, recipient=recipient,
                      message_type="request", status="pending",
                      payload_type=payload_type, payload=body)


def response_message(*, request: A2AMessage, sender: AgentId,
                     payload_type: str, payload: BaseModel | dict[str, Any]) -> A2AMessage:
    """Create a completed response correlated to an earlier request."""
    if request.message_type != "request" or request.recipient != sender:
        raise ValueError("response sender must be the recipient of a request message")
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return A2AMessage(correlation_id=request.correlation_id, sender=sender,
                      recipient=request.sender, message_type="response", status="completed",
                      payload_type=payload_type, payload=body)


def error_message(*, request: A2AMessage, sender: AgentId, code: str, message: str,
                  retryable: bool = False, details: dict[str, Any] | None = None) -> A2AMessage:
    """Create a machine-readable failure correlated to an earlier request."""
    if request.message_type != "request" or request.recipient != sender:
        raise ValueError("error sender must be the recipient of a request message")
    return A2AMessage(
        correlation_id=request.correlation_id,
        sender=sender,
        recipient=request.sender,
        message_type="error",
        status="failed",
        payload_type="AgentError",
        error=A2AError(code=code, message=message, retryable=retryable,
                       details=details or {}),
    )
