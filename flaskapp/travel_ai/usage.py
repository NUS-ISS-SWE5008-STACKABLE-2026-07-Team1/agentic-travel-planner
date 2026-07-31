"""Token-usage callback for one model invocation."""

from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler


class TokenUsageCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0

    def on_llm_end(self, response, **_kwargs) -> None:
        usage = {}
        try:
            message = response.generations[0][0].message
            usage = getattr(message, "usage_metadata", None) or {}
        except (AttributeError, IndexError, TypeError):
            pass
        if not usage:
            raw = (getattr(response, "llm_output", None) or {}).get("token_usage", {})
            usage = {
                "input_tokens": raw.get("prompt_tokens", 0),
                "output_tokens": raw.get("completion_tokens", 0),
                "total_tokens": raw.get("total_tokens", 0),
            }
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)
        self.total_tokens += int(
            usage.get("total_tokens", self.input_tokens + self.output_tokens) or 0
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }
