"""Shared model construction for the demo scripts.

The demos were written against an earlier config that exposed
`Config.OPENAI_API_KEY` / `Config.OPENAI_MODEL` directly. This repo resolves a
provider first (`config.get_llm_settings`), supports eight of them, and keeps
credentials in `.env.secrets`. Rather than teach each demo that dispatch, both
go through here — the same provider handling `travel_ai/service.py` performs.
"""

from __future__ import annotations

import sys

from flaskapp.config import Config, get_llm_settings


def use_utf8_stdout() -> None:
    """Stop a Windows console from killing a demo mid-print.

    Model rationales routinely contain characters cp1252 cannot encode (a
    non-breaking hyphen is enough), and the default Windows console encoding
    turns that into a UnicodeEncodeError *after* the API call has been paid
    for. Call this before printing model output.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def build_demo_llm():
    """A chat model from the configured provider, or `(None, reason)`.

    Returns `(llm, None)` on success and `(None, reason)` when no usable
    credential is configured, so a demo can print the reason and exit cleanly
    instead of raising at import time.
    """
    settings, error = get_llm_settings({key: getattr(Config, key) for key in dir(Config) if key.isupper()})
    if settings is None:
        return None, error

    provider = settings["provider"]
    options = {"api_key": settings["api_key"], "timeout": settings["timeout"], "max_retries": 2}
    if settings.get("temperature") is not None:
        options["temperature"] = settings["temperature"]

    if provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            **options,
            azure_endpoint=settings["endpoint"],
            azure_deployment=settings["model"],
            api_version=settings["api_version"],
        ), None
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(**options, model=settings["model"]), None
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            google_api_key=settings["api_key"], model=settings["model"],
            timeout=settings["timeout"], max_retries=2,
        ), None

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(**options, model=settings["model"], base_url=settings.get("base_url")), None
