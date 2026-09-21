"""Provider-neutral chat-model construction.

Extracted from TravelPlanningService so the orchestrator's intake step builds its
client the same way the planning graph does. Adding a provider means editing one
place.
"""

from __future__ import annotations


def build_llm(
    *, provider: str, api_key: str, model: str, temperature: float | None = None,
    timeout: float = 180, endpoint: str | None = None, api_version: str | None = None,
    base_url: str | None = None, max_retries: int = 2, **_ignored,
):
    """Return a configured LangChain chat model for the selected provider.

    `timeout` bounds ONE attempt, and `max_retries` decides how many there are,
    so the worst case a caller waits is roughly the product of the two. Planning
    keeps the default 2 retries: a plan already costs 30-90s and a transient 429
    is worth re-sending. The guardrail gates pass 0, because their timeouts are
    budgets a traveller waits on (see `guardrails.classifier.guardrail_settings`).
    """
    client_options = {"api_key": api_key, "timeout": timeout, "max_retries": max_retries}
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
            "timeout": timeout, "max_retries": max_retries,
        }
        if temperature is not None:
            google_options["temperature"] = temperature
        return ChatGoogleGenerativeAI(**google_options)
    raise ValueError(f"Unsupported LLM provider: {provider}")
