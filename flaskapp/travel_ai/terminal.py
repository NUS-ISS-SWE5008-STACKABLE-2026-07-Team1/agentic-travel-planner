"""Readable terminal output for local workflow observation."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


logger = logging.getLogger("atlas.agent_activity")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def log_payload(label: str, payload: Any) -> None:
    """Log validated structured data without prompts, credentials, or chain-of-thought."""
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    logger.info("%s\n%s", label, json.dumps(payload, ensure_ascii=False, indent=2, default=str))
