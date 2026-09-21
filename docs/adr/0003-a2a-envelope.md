# ADR 0003: A versioned envelope is the only cross-agent interface

**Status:** Accepted — `flaskapp/travel_ai/a2a.py`

## Context

Five agents exchange results. The tempting interface between two LLM-backed
components is free text, or a loosely-shaped dict — both are what the model
already emits.

Both are also untyped, unversioned, and unauditable, and they make the boundary
impossible to move: you cannot put an agent behind HTTP if its interface is
"whatever the caller happened to put in the dict".

## Options

1. **Free text / raw dicts** between agents.
2. **A typed Pydantic envelope**, transport-neutral, with correlation IDs.
3. **A full message-bus schema registry** (Avro/Protobuf + broker).

## Decision

Option 2. Every handoff is an `A2AMessage`: `protocol_version`, `message_id`,
`correlation_id`, `sender`, `recipient`, `message_type`, `status`,
`payload_type`, `payload`, `error`. `extra="forbid"` throughout.

Constructed only through `request_message` / `response_message` /
`error_message`. All three are keyword-only, and the latter two take the
original request so they derive `correlation_id` from it and *reject a sender
that was not its recipient* — the envelope enforces conversational sanity, not
just field types.

Option 3 was rejected as premature: a schema registry solves cross-team,
cross-language contract drift, and this is one team in one language. Pydantic
gives the validation without the infrastructure.

## Consequences

- **The envelope is transport-neutral, which is what makes decomposition
  possible at all.** The same message travels an in-process call or HTTP; see
  [ADR 0004](0004-composition-time-binding.md).
- Malformed handoffs fail at the boundary that produced them, not three agents
  downstream.
- `correlation_id` ties every message in one plan together, which is what makes
  the `a2a_messages` table and the hash-chained trace reconstructable.
- Adding an agent touches three registries (`AgentId`, `AgentFinding.agent`,
  `SPECIALIST_NODE_FACTORIES`). Deliberate friction — an unregistered agent
  cannot silently join the conversation.
- `payload` must never carry prompts, secrets, chain-of-thought, or unnecessary
  personal data. This is a documented rule, **not currently an enforced one** —
  a validator that rejects known-sensitive keys is an open gap.
