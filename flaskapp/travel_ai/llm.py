"""Provider-neutral chat-model construction.

Extracted from TravelPlanningService so the orchestrator's intake step builds its
client the same way the planning graph does. Adding a provider means editing one
place.
"""

from __future__ import annotations


def build_llm(
    *, provider: str, api_key: str, model: str, temperature: float | None = None,
    timeout: float = 180, endpoint: str | None = None, api_version: str | None = None,
    base_url: str | None = None, **_ignored,
):
    """Return a configured LangChain chat model for the selected provider."""
    client_options = {"api_key": api_key, "timeout": timeout, "max_retries": 2}
    if temperature is not None:
        client_options["temperature"] = temperature
    if provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(**client_options, azure_endpoint=endpoint,
                               azure_deployment=model, api_version=api_version)
    if provider in {"openai", "deepseek", "xai", "meta", "openai_compatible"}:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(**client_options, model=model, base_url=base_url)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(**client_options, model=model)
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        google_options = {
            "google_api_key": api_key, "model": model,
            "timeout": timeout, "max_retries": 2,
        }
        if temperature is not None:
            google_options["temperature"] = temperature
        return ChatGoogleGenerativeAI(**google_options)
    raise ValueError(f"Unsupported LLM provider: {provider}")
