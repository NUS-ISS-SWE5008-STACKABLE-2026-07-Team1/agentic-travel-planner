"""Shared administrator authorization helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_admin_email(config: Mapping[str, Any], email: str | None) -> bool:
    normalized = (email or "").strip().lower()
    configured = {item.strip().lower() for item in config.get("ADMIN_EMAILS", ()) if item.strip()}
    legacy = str(config.get("ADMIN_EMAIL", "")).strip().lower()
    if legacy:
        configured.add(legacy)
    if normalized and normalized in configured:
        return True
    if normalized and config.get("DATABASE"):
        from flaskapp.database import is_database_admin
        return is_database_admin(config["DATABASE"], normalized)
    return False
