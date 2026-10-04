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
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        # This call's own sum when the provider omits a total. Using the running
        # totals here would re-add every earlier call's tokens on each new one.
        self.total_tokens += int(usage.get("total_tokens") or input_tokens + output_tokens)

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }
