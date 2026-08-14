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

    def __init__(self, trace_dir: Path, request_id: str, database_path: Path | None = None):
        self.path = trace_dir / f"{request_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.request_id = request_id
        self._previous_hash = "GENESIS"
        self.database_path = database_path

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
            if self.database_path:
                from flaskapp.database import save_audit_event
                save_audit_event(self.database_path, item)
            self._previous_hash = item["hash"]


def verify_hash_chain(path: Path) -> bool:
    """Recompute every event's hash and confirm the chain is unbroken.

    True iff each event's stored hash matches its recomputed hash and each
    event's previous_hash matches the prior event's actual hash (GENESIS for
    the first). A mismatch means the trace file was edited or reordered after
    the fact — the post-response gate for the explainability substrate, which
    presumes the trace an explanation is built from has not been tampered with.
    """
    previous_hash = "GENESIS"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        stored_hash = item.pop("hash", None)
        if item.get("previous_hash") != previous_hash:
            return False
        canonical = json.dumps(item, sort_keys=True, default=str)
        recomputed = hashlib.sha256(canonical.encode()).hexdigest()
        if recomputed != stored_hash:
            return False
        previous_hash = recomputed
    return True
