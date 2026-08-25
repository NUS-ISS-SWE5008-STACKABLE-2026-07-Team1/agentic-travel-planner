"""Shared text detectors: prompt injection, bias, toxicity.

Domain-agnostic pattern matching, usable by any agent's own input/output
screening. Grounding checks (verifying an agent's specific factual claims)
stay in each agent's own guardrails module, since those are inherently
agent-specific.
"""

from __future__ import annotations

import re
from typing import Any

INJECTION_PATTERNS = [
    r"(?i)\bignore (all |any |the )?previous instructions\b",
    r"(?i)\b(disable|bypass|override|evade) (the )?(guardrails|policy|governance|controls)\b",
    r"(?i)\b(show|reveal|print|dump) (the )?(system prompt|hidden prompt|policy secrets)\b",
    r"(?i)\bact as (an? )?(unrestricted|unfiltered|developer mode)\b",
]


def screen_preferences(preferences: list[str]) -> list[str]:
    """Which free-text entries look instruction-like. Empty list = clean."""
    return [text for text in preferences if any(re.search(p, text) for p in INJECTION_PATTERNS)]


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


def _text_values(preferences: dict | list | None) -> list[str]:
    if preferences is None:
        return []
    values = preferences.values() if isinstance(preferences, dict) else preferences
    return [v for v in values if isinstance(v, str)]


def screen_input_text(preferences: dict | list | None, *extra_texts: list[str]) -> dict[str, Any]:
    texts = _text_values(preferences)
    for extra in extra_texts:
        texts.extend(_text_values(extra))
    injection_hits = [t for t in texts if screen_preferences([t])]
    bias_hits = [(t, detect_bias(t)) for t in texts]
    high_bias = [(t, b) for t, b in bias_hits if b["risk_level"] == "high"]
    toxicity_hits = [(t, detect_toxicity(t)) for t in texts]
    flagged_toxicity = [(t, tox) for t, tox in toxicity_hits if tox["flagged"]]

    blocked = bool(injection_hits or high_bias or flagged_toxicity)
    return {
        "blocked": blocked,
        "injection": injection_hits,
        "high_bias": high_bias,
        "toxicity": flagged_toxicity,
    }


def screen_output_text(text: str) -> dict[str, Any]:
    bias = detect_bias(text)
    toxicity = detect_toxicity(text)
    flagged = bias["risk_level"] == "high" or toxicity["flagged"]
    return {"flagged": flagged, "bias": bias, "toxicity": toxicity}
