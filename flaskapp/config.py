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
    # Official A2A 1.x specialist endpoints. The Flask application keeps its
    # browser API; specialists are exposed by a small ASGI sidecar so standard
    # A2A clients can discover and invoke them independently.
    A2A_BASE_URL = os.getenv("A2A_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    A2A_HOST = os.getenv("A2A_HOST", "127.0.0.1")
    A2A_PORT = int(os.getenv("A2A_PORT", "5000"))
    # The combined ASGI application turns this on at runtime so every
    # orchestrator-to-specialist call uses official A2A within one deployment.
    # Flask-only mode leaves it off because it hosts no A2A routes.
    A2A_INTERNAL_ENABLED = os.getenv(
        "A2A_INTERNAL_ENABLED", "false"
    ).lower() == "true"
    # Which agent answers at `/.well-known/agent-card.json`. The orchestrator is
    # the sensible front door for a caller who wants a trip planned; point this
    # at `flight_agent` to put that agent alone in front of a conformance run.
    A2A_ROOT_AGENT = os.getenv("A2A_ROOT_AGENT", "orchestrator_agent")
    # Whether an A2A caller may name the request id its work is recorded under.
    # Safe only when the endpoint is not reachable by strangers, because that id
    # is a write key into an existing audit trail. Enable it together with the
    # in-house orchestrator transport below.
    A2A_TRUST_CALLER_REQUEST_ID = os.getenv(
        "A2A_TRUST_CALLER_REQUEST_ID", "false"
    ).lower() == "true"

    # How the orchestrator reaches the flight agent. `inprocess` (default) calls
    # the node directly; `a2a` routes it over the real Agent2Agent protocol.
    # Resolved once when the graph is built, never per request — the same
    # one-variable-revert shape as FLIGHT_AGENT_MODE.
    FLIGHT_AGENT_TRANSPORT = os.getenv("FLIGHT_AGENT_TRANSPORT", "inprocess").strip().lower()
    FLIGHT_AGENT_A2A_URL = os.getenv("FLIGHT_AGENT_A2A_URL", "")
    FLIGHT_AGENT_A2A_TIMEOUT_SECONDS = float(
        os.getenv("FLIGHT_AGENT_A2A_TIMEOUT_SECONDS", "60")
    )
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
    # L1 PII redaction on the intake prompt, ahead of the L2 call. Unlike every
    # other guardrail this one never blocks: it removes the identifier and lets
    # planning continue, because a traveller who volunteers a phone number is
    # not an attacker and the trip does not need it. Off is a development
    # convenience only — with it off, identifiers a traveller types reach the
    # classifier, the extraction model, and the intake_messages table.
    PII_REDACTION_ENABLED = os.getenv("PII_REDACTION_ENABLED", "true").lower() == "true"
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

    # --- Flight Agent execution mode and loop budgets ---
    #
    # "auto" (default), "structured", or "agentic".
    #
    # `structured` is the single-shot path: domain.py searches once and the model
    # explains the result. `agentic` always opens the tool-calling loop. `auto`
    # runs structured unless the deterministic search leaves a leg empty, and only
    # then opens the loop.
    #
    # Default is `auto` on the evidence in docs/flight_agent/mode-eval.md: across
    # 24 live runs the loop's benefit was concentrated in one scenario (a stocked
    # route on an unstocked date, 0 options -> 6), while every other covered
    # scenario produced an identical option count for 2-4 extra seconds. The
    # escalation test costs no model call, so `auto` keeps single-shot latency on
    # the common path and spends the loop only where it changes the answer.
    FLIGHT_AGENT_MODE = os.getenv("FLIGHT_AGENT_MODE", "auto").strip().lower()
    # Model turns inside the loop, excluding the terminal structured turn that
    # always follows. Three is enough for search, widen, conclude.
    FLIGHT_AGENT_MAX_LLM_TURNS = int(os.getenv("FLIGHT_AGENT_MAX_LLM_TURNS", "3"))
    FLIGHT_AGENT_MAX_TOOL_CALLS = int(os.getenv("FLIGHT_AGENT_MAX_TOOL_CALLS", "6"))
    # Calls to `provider.fetch`, NOT supplier searches. `duffel.fetch` fans out
    # over airport pairs and can issue up to MAX_AIRPORTS_PER_CITY ** 2 = 4 billed
    # POSTs per call, so 2 here can mean 8 billed searches. Sized with that in
    # mind rather than renamed, because `fetch` is the only unit the cache can
    # observe. Free on seed, which returns its whole dataset in one call.
    FLIGHT_AGENT_MAX_PROVIDER_CALLS = int(os.getenv("FLIGHT_AGENT_MAX_PROVIDER_CALLS", "2"))
    # Wall clock for the whole loop. Deliberately far below
    # AI_REQUEST_TIMEOUT_SECONDS: that bounds one model call, this bounds a
    # sequence of them, and a traveller is waiting on the slowest specialist.
    FLIGHT_AGENT_LOOP_DEADLINE_SECONDS = float(
        os.getenv("FLIGHT_AGENT_LOOP_DEADLINE_SECONDS", "45")
    )
    # How far a search may move a leg from the traveller's own date. Measured
    # against the original request, so repeated shifts cannot accumulate.
    FLIGHT_AGENT_MAX_DATE_SHIFT_DAYS = int(
        os.getenv("FLIGHT_AGENT_MAX_DATE_SHIFT_DAYS", "3")
    )
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
