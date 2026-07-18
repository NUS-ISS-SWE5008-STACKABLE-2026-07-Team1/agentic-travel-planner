"""Tamper-evident, privacy-conscious JSONL audit tracing."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_lock = threading.Lock()


class AuditTracer:
    """Append agent lifecycle events with a hash chain for accountability."""

    def __init__(self, trace_dir: Path, request_id: str):
        self.path = trace_dir / f"{request_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.request_id = request_id
        self._previous_hash = "GENESIS"

    def record(self, event: str, agent: str, details: dict[str, Any] | None = None) -> None:
        # Do not record prompts, API keys, or raw personal data in production traces.
        # Specialist nodes may finish concurrently, so hash calculation and writing
        # must share the same critical section to preserve a single valid chain.
        with _lock:
            item = {
                "timestamp": datetime.now(UTC).isoformat(),
                "request_id": self.request_id,
                "event": event,
                "agent": agent,
                "details": details or {},
                "previous_hash": self._previous_hash,
            }
            canonical = json.dumps(item, sort_keys=True, default=str)
            item["hash"] = hashlib.sha256(canonical.encode()).hexdigest()
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, default=str) + "\n")
            self._previous_hash = item["hash"]
