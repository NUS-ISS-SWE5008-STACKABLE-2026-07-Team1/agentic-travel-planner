"""Environment-backed application settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_PROJECT_ROOT / "crediential.env", override=True)


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

    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure").lower()
    DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5")
    AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    AZURE_OPENAI_TEMPERATURE = _optional_float("AZURE_OPENAI_TEMPERATURE")
    AI_REQUEST_TIMEOUT_SECONDS = float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "180"))
    TRACE_DIR = Path(os.getenv("TRACE_DIR", "instance/traces"))
    DATABASE = Path(os.getenv("DATABASE", "instance/travel_planner.sqlite3"))
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
