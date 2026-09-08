"""Canonical, shared bias and toxicity detectors.

This is the common-layer home for bias/toxicity pattern matching. It exists
because the same detection logic (two-tier bias, word-bounded toxicity terms)
was previously only reachable by importing from `agents/flight_agent/
guardrails.py` — a module owned by, and named for, one specific agent. Any
agent's own screening module should hold its own copy of what it needs (see
each agent's own `guardrails.py`, and the review this module accompanies) —
what belongs here is a single, neutral, well-tested reference implementation
that isn't privately owned by any one agent, for whoever wants to fork from a
known-good starting point instead of inventing bias/toxicity rules from
scratch.

This module does not replace or modify any existing agent's guardrails file.
Nothing currently imports from it. It is a common-layer asset, offered for
agents to fork from going forward.
"""

from __future__ import annotations

import re
from typing import Any

# --- Bias: two-tier, matching the reasoning already validated by the eval
# report (docs/security/guardrail-eval-report.md) — a bare protected-attribute
# mention is "medium" and not blocked (destinations/cuisines/accessibility
# needs legitimately name nationality, religion, age, disability); only
# attribute + stereotype-trigger together is "high" and blocked. ---

BIAS_ATTRIBUTE_PATTERNS: dict[str, list[str]] = {
    "gender": [r"\b(men|women|male|female|non[- ]?binary|transgender)\b"],
    "race_ethnicity": [r"\b(race|racial|ethnic|ethnicity|black|white|asian|indian|malay|chinese)\b"],
    "culture_nationality": [r"\b(foreigner|foreigners|nationality|culture|immigrant|immigrants|singaporean)\b"],
    "religion": [r"\b(christian|christians|muslim|muslims|jewish|jews|hindu|hindus|buddhist|buddhists|religion|faith)\b"],
    "age": [r"\b(elderly|older people|young people|teenagers|boomers|gen z)\b"],
    "disability_health": [r"\b(disabled|disability|mental illness|psychiatric condition)\b"],
}
STEREOTYPE_TRIGGER_PATTERNS = [
    r"\b(all|always|never|naturally)\b",
    r"\b(inferior|superior)\b",
    r"\b(better at|worse at|not suitable|should not|cannot|can't)\b",
]


def detect_bias(text: str) -> dict[str, Any]:
    """Two-tier bias risk over a single string.

    "high" (attribute + stereotype trigger together) is the only level a
    caller should treat as blocking. "medium" (attribute alone) is a normal
    outcome for travel content and must stay unblocked, or ordinary requests
    like "halal food near a mosque" become false positives.
    """
    attributes = [
        name
        for name, patterns in BIAS_ATTRIBUTE_PATTERNS.items()
        if any(re.search(p, text, re.IGNORECASE) for p in patterns)
    ]
    stereotype_signal = any(re.search(p, text, re.IGNORECASE) for p in STEREOTYPE_TRIGGER_PATTERNS)
    if attributes and stereotype_signal:
        risk_level = "high"
    elif attributes:
        risk_level = "medium"
    else:
        risk_level = "low"
    return {
        "risk_level": risk_level,
        "attributes_detected": sorted(set(attributes)),
        "stereotype_signal": stereotype_signal,
    }


# --- Toxicity: word-bounded keyword matching. Bare substring matching is a
# live false-positive source in travel content ("kill" inside "Kilimanjaro",
# "die" inside "diet"/"Dieppe") — every term below is matched as a whole word.

TOXICITY_TERMS: dict[str, list[str]] = {
    "toxicity": ["stupid", "idiot", "hate", "trash", "useless"],
    "severe_toxicity": ["kill", "destroy them", "die"],
    "insult": ["idiot", "stupid", "dumb", "moron"],
    "identity_attack": ["all women", "all men", "all asians", "all foreigners", "all muslims", "all christians"],
}

_TOXICITY_PATTERNS: dict[str, list[re.Pattern]] = {
    label: [re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in terms]
    for label, terms in TOXICITY_TERMS.items()
}


def detect_toxicity(text: str) -> dict[str, Any]:
    flagged_labels = sorted(
        label for label, patterns in _TOXICITY_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    )
    return {"flagged": bool(flagged_labels), "labels": flagged_labels}
