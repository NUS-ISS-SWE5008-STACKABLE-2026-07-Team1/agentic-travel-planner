from types import SimpleNamespace

from flaskapp.travel_ai.usage import TokenUsageCallback


def test_token_usage_callback_captures_azure_message_metadata():
    callback = TokenUsageCallback()
    response = SimpleNamespace(generations=[[
        SimpleNamespace(message=SimpleNamespace(usage_metadata={
            "input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
        }))
    ]])
    callback.on_llm_end(response)
    assert callback.as_dict() == {
        "input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
    }
