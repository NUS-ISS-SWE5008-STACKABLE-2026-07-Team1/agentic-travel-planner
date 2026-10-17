"""DRAFT — backlog item 14. Destination: scripts/ci_stub_provider.py

A minimal OpenAI-compatible chat-completions endpoint for CI.

Why this exists
---------------
The DAST job needs the app to actually work. Without a model,
POST /api/v1/travel-plans fails immediately and ZAP only ever sees the error
path, so the LangGraph run and the plan-rendering templates are never scanned.

Using a real provider key instead would be the wrong fix: it puts a live
credential in repository secrets, which is precisely what a hijacked action
exfiltrates, and it makes every unmocked test one refactor away from a billed
call. See backlog items 4, 10 and 14.

How it satisfies structured output
----------------------------------
Every agent goes through ``with_structured_output(..., method="json_schema")``,
so the request carries the expected schema at
``response_format.json_schema.schema``. Rather than hardcoding a reply per
agent, this stub reads that schema and synthesises a minimal valid instance.
That means it keeps working when the Pydantic models change.

Stdlib only — no new entry in requirements.txt.

STATUS
------
Schema synthesis is VERIFIED: instances generated from
``TravelPlan``, ``AgentFinding`` and ``FlightAgentResponse``
(``model_json_schema()`` -> ``synthesise()`` -> ``model_validate()``) all
validate cleanly.

What is NOT yet verified is the HTTP layer end to end — whether
langchain-openai accepts this response envelope and whether the app renders a
plan built from placeholder values. Verify locally before merging:

    python scripts/ci_stub_provider.py &
    LLM_PROVIDER=openai_compatible LLM_API_KEY=stub \\
    LLM_BASE_URL=http://127.0.0.1:8081/v1 LLM_MODEL=stub-model python app.py

then submit a plan through the UI and confirm it renders.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

PORT = 8081

# Values chosen to be plausible for this domain, so a rendered plan looks like
# a plan rather than a wall of "string". Ranking/consistency assertions must
# never depend on these — that is what the deterministic tests are for.
_PLACEHOLDER_STRING = "ci-stub"
_PLACEHOLDER_NUMBER = 1


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Follow a local $ref one hop. Pydantic emits #/$defs/Name references."""
    ref = schema.get("$ref")
    if not ref or not ref.startswith("#/"):
        return schema
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return schema
        node = node[part]
    return node if isinstance(node, dict) else schema


def synthesise(schema: dict[str, Any], root: dict[str, Any] | None = None) -> Any:
    """Build the smallest instance that satisfies `schema`.

    Only the subset Pydantic actually emits is handled: object, array, string,
    integer/number, boolean, null, enum, anyOf/oneOf, and $ref.
    """
    root = root if root is not None else schema
    schema = _resolve(schema, root)

    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]

    for key in ("anyOf", "oneOf"):
        if key in schema and schema[key]:
            # Prefer a non-null branch so required fields get real values.
            branches = schema[key]
            chosen = next(
                (b for b in branches if _resolve(b, root).get("type") != "null"),
                branches[0],
            )
            return synthesise(chosen, root)

    kind = schema.get("type")

    if kind == "object" or "properties" in schema:
        properties: dict[str, Any] = schema.get("properties", {})
        # Emitting every property, not just required ones, keeps optional
        # fields populated so the templates have something to render.
        return {name: synthesise(sub, root) for name, sub in properties.items()}

    if kind == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            return []
        count = max(int(schema.get("minItems", 1)), 1)
        return [synthesise(items, root) for _ in range(count)]

    if kind == "integer":
        return _PLACEHOLDER_NUMBER
    if kind == "number":
        return float(_PLACEHOLDER_NUMBER)
    if kind == "boolean":
        return False
    if kind == "null":
        return None

    return _PLACEHOLDER_STRING


def _content_for(body: dict[str, Any]) -> str:
    """Return the assistant message content the caller expects."""
    response_format = body.get("response_format") or {}
    json_schema = response_format.get("json_schema") or {}
    schema = json_schema.get("schema")

    if isinstance(schema, dict):
        return json.dumps(synthesise(schema))

    # No structured output requested — plain prose is fine.
    return "CI stub response. No live model was called."


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") in ("/healthz", "/v1/healthz"):
            self._send(200, {"status": "ok"})
            return
        if self.path.rstrip("/") == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": "stub-model", "object": "model"}]})
            return
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": "not found"}})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "invalid JSON"}})
            return

        content = _content_for(body)
        self._send(200, {
            "id": "chatcmpl-ci-stub",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "stub-model"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def log_message(self, *_args: Any) -> None:
        """Silence per-request logging.

        Deliberate: this process's stdout would otherwise be captured into a
        build artefact, and request bodies contain the traveller payload the
        app just sent. See backlog item 15.
        """


if __name__ == "__main__":
    print(f"CI stub provider listening on http://127.0.0.1:{PORT}/v1", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
