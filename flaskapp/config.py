"""Environment-backed application settings."""

from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Checked-in code contains no credentials. Local files are ignored by Git and
# process environment variables always win over file-based development settings.
for _env_file in (".env", ".env.local", ".env.secrets", "crediential.env"):
    load_dotenv(_PROJECT_ROOT / _env_file, override=False)


def _optional_float(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    return float(value) if value else None

_DEMO_PASSWORD_HASH = (
    "scrypt:32768:8:1$99T3BfVwYO8CnqNC$"
    "c85a15f2f167616564085724c37c79fc2ad151e306e5ec0414759a0f8a6eba28"
    "a179494a8d39bfd838986ebbb2daa4d0586da0bee718d299fce4a89a7de45a95"
)


class Config:
    """Safe defaults; secrets must be supplied through environment variables."""

    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
    LLM_MODEL = os.getenv("LLM_MODEL")
    LLM_TEMPERATURE = _optional_float("LLM_TEMPERATURE")
    DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5")
    AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    AZURE_OPENAI_TEMPERATURE = _optional_float("AZURE_OPENAI_TEMPERATURE")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    XAI_API_KEY = os.getenv("XAI_API_KEY")
    META_API_KEY = os.getenv("META_API_KEY")
    META_BASE_URL = os.getenv("META_BASE_URL")
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL")
    AI_REQUEST_TIMEOUT_SECONDS = float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "180"))
    # L2 guardrails: the LLM classifier that screens traveller free text and
    # generated plans for what the regex layer cannot see (obfuscated injection,
    # role-play jailbreaks, out-of-scope requests). Set enabled=false to develop
    # offline; the deterministic L0/L1 gates keep working either way.
    GUARDRAIL_LLM_ENABLED = os.getenv("GUARDRAIL_LLM_ENABLED", "true").lower() == "true"
    # Shared default for both gates; blank inherits LLM_MODEL. Point this at the
    # provider's small/fast tier: the classifier runs on the critical path twice
    # per plan, and a cheaper model is usually better at this narrow task per
    # unit of latency.
    GUARDRAIL_LLM_MODEL = os.getenv("GUARDRAIL_LLM_MODEL")
    # The two gates have opposite requirements, so each can override the shared
    # default. Blank inherits GUARDRAIL_LLM_MODEL.
    #
    # INPUT runs before anything else, with the traveller waiting on a blank
    # screen, and reads adversarial text. It wants fast and hard to talk out of
    # its instructions — a small model, and under fail-closed its p95 latency is
    # an availability number, not a comfort one.
    #
    # OUTPUT runs after roughly 75 seconds of planning, so two extra seconds is
    # noise, and it judges our own model's prose for ungrounded claims and bias
    # rather than fending off an attacker. It can afford nuance.
    #
    # Which model belongs in which slot is a measurement, not a principle:
    # `scripts/guardrail_eval.py` reports per-gate recall, false positives on
    # the benign half, and latency. Set these from that report.
    GUARDRAIL_INPUT_LLM_MODEL = os.getenv("GUARDRAIL_INPUT_LLM_MODEL")
    GUARDRAIL_OUTPUT_LLM_MODEL = os.getenv("GUARDRAIL_OUTPUT_LLM_MODEL")
    # Deliberately NOT AI_REQUEST_TIMEOUT_SECONDS. A 180-second guardrail is not
    # a guardrail: under fail-closed it converts a slow provider into a
    # three-minute hang before the traveller is told no. This one governs the
    # INPUT gate.
    GUARDRAIL_LLM_TIMEOUT_SECONDS = float(os.getenv("GUARDRAIL_LLM_TIMEOUT_SECONDS", "8"))
    # The output gate's own budget, INDEPENDENT of the value above: raising the
    # input timeout does not raise this one, and vice versa. Larger because the
    # traveller has already waited through planning by the time it runs, and
    # because a timeout here throws away a plan that five agent calls just paid
    # for. Still bounded — under fail-closed an unbounded gate is an outage.
    GUARDRAIL_OUTPUT_LLM_TIMEOUT_SECONDS = float(
        os.getenv("GUARDRAIL_OUTPUT_LLM_TIMEOUT_SECONDS", "20")
    )
    # A "block" below this confidence is downgraded to a flag: audited, but the
    # traveller is not denied. Set it from the eval curve in
    # docs/security/guardrail-eval-report.md rather than by intuition.
    GUARDRAIL_BLOCK_THRESHOLD = float(os.getenv("GUARDRAIL_BLOCK_THRESHOLD", "0.7"))
    # "closed" (default): a classifier error blocks the request. "open": it
    # flags and proceeds, relying on L0/L1 alone. Fail-open is a deliberate,
    # visible choice, never a silent fallback.
    GUARDRAIL_FAIL_MODE = os.getenv("GUARDRAIL_FAIL_MODE", "closed").strip().lower()
    # Where Flight Agent's inventory comes from: "seed" (the project's static
    # dataset, the default and what every golden scenario is pinned to) or
    # "duffel" (live supplier search). Selecting duffel without a token falls
    # back to seed with a visible warning rather than failing.
    FLIGHT_INVENTORY_SOURCE = os.getenv("FLIGHT_INVENTORY_SOURCE", "seed").strip().lower()
    DUFFEL_API_TOKEN = os.getenv("DUFFEL_API_TOKEN")
    DUFFEL_API_VERSION = os.getenv("DUFFEL_API_VERSION", "v2")
    DUFFEL_TIMEOUT_SECONDS = float(os.getenv("DUFFEL_TIMEOUT_SECONDS", "30"))
    # Duffel's own cap on how long it waits for airlines, in ms (2000-60000).
    # Keep it below DUFFEL_TIMEOUT_SECONDS so Duffel returns partial results
    # before our HTTP client gives up on it.
    DUFFEL_SUPPLIER_TIMEOUT_MS = int(os.getenv("DUFFEL_SUPPLIER_TIMEOUT_MS", "20000"))
    DUFFEL_MAX_OFFERS = int(os.getenv("DUFFEL_MAX_OFFERS", "50"))
    TRACE_DIR = Path(os.getenv("TRACE_DIR", "instance/traces"))
    # A DSN wins when present; otherwise the local SQLite file. Keeping both in
    # one setting is what lets every database function take one target argument.
    DATABASE = os.getenv("DATABASE_URL", "").strip() or Path(
        os.getenv("DATABASE", "instance/travel_planner.sqlite3")
    )
    MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "12000"))
    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-change-me")
    LOGIN_EMAIL = os.getenv("LOGIN_EMAIL", "demo@example.com").lower()
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", LOGIN_EMAIL).lower()
    ADMIN_EMAILS = tuple(
        email.strip().lower()
        for email in os.getenv("ADMIN_EMAILS", "").split(",")
        if email.strip()
    )
    # Demo password: TravelDemo2026! Replace this hash through the environment.
    LOGIN_PASSWORD_HASH = os.getenv("LOGIN_PASSWORD_HASH", _DEMO_PASSWORD_HASH)
    if LOGIN_PASSWORD_HASH == "replace-with-a-generated-werkzeug-password-hash":
        LOGIN_PASSWORD_HASH = _DEMO_PASSWORD_HASH
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8
    JSON_SORT_KEYS = False
    PROPAGATE_EXCEPTIONS = False


def get_llm_settings(config: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Build provider-neutral settings without logging or returning secret values in errors."""
    provider = str(config.get("LLM_PROVIDER", "auto")).lower()
    provider = "azure_openai" if provider == "azure" else provider
    if provider == "auto":
        candidates = [
            ("azure_openai", config.get("AZURE_OPENAI_API_KEY")),
            ("openai", config.get("OPENAI_API_KEY")),
            ("anthropic", config.get("ANTHROPIC_API_KEY")),
            ("google", config.get("GOOGLE_API_KEY")),
            ("deepseek", config.get("DEEPSEEK_API_KEY")),
            ("xai", config.get("XAI_API_KEY")),
            ("meta", config.get("META_API_KEY")),
            ("openai_compatible", config.get("LLM_API_KEY")),
        ]
        configured = [name for name, key in candidates if key]
        if len(configured) != 1:
            return None, "Set exactly one provider credential or choose LLM_PROVIDER explicitly"
        provider = configured[0]
    default_models = {
        "azure_openai": config.get("AZURE_OPENAI_DEPLOYMENT"),
        "openai": "gpt-5",
        "anthropic": "claude-sonnet-4-6",
        "google": "gemini-3.5-flash",
        "deepseek": "deepseek-v4-flash",
        "xai": "grok-4.3",
    }
    common = {
        "provider": provider,
        "model": config.get("LLM_MODEL") or default_models.get(provider),
        "temperature": config.get("LLM_TEMPERATURE")
        if config.get("LLM_TEMPERATURE") is not None else config.get("AZURE_OPENAI_TEMPERATURE"),
        "timeout": config.get("AI_REQUEST_TIMEOUT_SECONDS", 180),
    }
    if provider == "azure_openai":
        required = (config.get("AZURE_OPENAI_API_KEY"), config.get("AZURE_OPENAI_ENDPOINT"), common["model"])
        if not all(required):
            return None, "Azure OpenAI configuration is incomplete"
        return {**common, "api_key": required[0], "endpoint": required[1],
                "api_version": config.get("AZURE_OPENAI_API_VERSION")}, None
    if provider == "openai":
        if not config.get("OPENAI_API_KEY") or not common["model"]:
            return None, "OpenAI configuration is incomplete"
        return {**common, "api_key": config["OPENAI_API_KEY"],
                "base_url": config.get("OPENAI_BASE_URL")}, None
    if provider == "anthropic":
        if not config.get("ANTHROPIC_API_KEY") or not common["model"]:
            return None, "Anthropic configuration is incomplete"
        return {**common, "api_key": config["ANTHROPIC_API_KEY"]}, None
    if provider == "google":
        if not config.get("GOOGLE_API_KEY") or not common["model"]:
            return None, "Google Gemini configuration is incomplete"
        return {**common, "api_key": config["GOOGLE_API_KEY"]}, None
    compatible = {
        "deepseek": (config.get("DEEPSEEK_API_KEY"), "https://api.deepseek.com"),
        "xai": (config.get("XAI_API_KEY"), "https://api.x.ai/v1"),
        "meta": (config.get("META_API_KEY"), config.get("META_BASE_URL")),
        "openai_compatible": (config.get("LLM_API_KEY"), config.get("LLM_BASE_URL")),
    }
    if provider in compatible:
        key, base_url = compatible[provider]
        if not key or not base_url or not common["model"]:
            return None, f"{provider.replace('_', ' ').title()} configuration is incomplete"
        return {**common, "api_key": key, "base_url": base_url}, None
    return None, f"Unsupported LLM provider: {provider}"
