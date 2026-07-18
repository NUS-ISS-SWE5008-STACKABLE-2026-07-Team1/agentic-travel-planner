"""Environment-backed application settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Safe defaults; secrets must be supplied through environment variables."""

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0"))
    AI_REQUEST_TIMEOUT_SECONDS = float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "60"))
    TRACE_DIR = Path(os.getenv("TRACE_DIR", "instance/traces"))
    MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "12000"))
    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-change-me")
    LOGIN_EMAIL = os.getenv("LOGIN_EMAIL", "demo@example.com").lower()
    # Demo password: TravelDemo2026! Replace this hash through the environment.
    LOGIN_PASSWORD_HASH = os.getenv(
        "LOGIN_PASSWORD_HASH",
        "scrypt:32768:8:1$99T3BfVwYO8CnqNC$c85a15f2f167616564085724c37c79fc2ad151e306e5ec0414759a0f8a6eba28a179494a8d39bfd838986ebbb2daa4d0586da0bee718d299fce4a89a7de45a95",
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8
    JSON_SORT_KEYS = False
    PROPAGATE_EXCEPTIONS = False
