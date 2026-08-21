"""Bounded, thread-safe verdict cache keyed on a digest of the judged text.

Worth its own module for two reasons beyond saving tokens: the eval harness
replays the same corpus repeatedly, and the "Refine your plan" panel re-posts
the whole payload on every refinement (`static/js/app.js:286`), so a traveller
who refines three times would otherwise pay to re-screen identical preferences.

Only the digest is retained, never the text — the cache is process memory that
outlives a request, and traveller free text should not accumulate there.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict

from flaskapp.travel_ai.guardrails.types import Verdict

_MAX_ENTRIES = 512

_lock = threading.Lock()
_entries: OrderedDict[str, Verdict] = OrderedDict()


def cache_key(kind: str, prompt_version: str, model: str | None, text: str) -> str:
    """Digest the judged text together with everything that judged it.

    `prompt_version` is part of the key so editing the classifier instructions
    invalidates old verdicts rather than silently serving judgements made under
    different rules.

    `model` is part of it for the same reason, and one more: the input and
    output gates may run different models, and the eval harness compares models
    over an identical corpus in a single process. Without the model in the key,
    the second model measured would score whatever the first one decided.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{kind}:{prompt_version}:{model or 'default'}:{digest}"


def get(key: str) -> Verdict | None:
    with _lock:
        verdict = _entries.get(key)
        if verdict is None:
            return None
        _entries.move_to_end(key)
        return verdict


def put(key: str, verdict: Verdict) -> None:
    with _lock:
        _entries[key] = verdict
        _entries.move_to_end(key)
        while len(_entries) > _MAX_ENTRIES:
            _entries.popitem(last=False)


def clear() -> None:
    """Reset between tests, and after a config change in a long-lived process."""
    with _lock:
        _entries.clear()
