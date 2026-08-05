"""Flight Agent guardrails — pre-tool (input) and post-tool (output) gates.

Adopted from localfolder/XRAI/AI Agent 5b Chatbot with Guardrails and Policy (ext
yaml file).ipynb + its companion .yaml, read closely on 2026-07-20 (the earlier
session's deferred follow-up: the injection regex reused before was the generic
safeguards.py one, not this notebook's more precise pattern). That notebook's
core design, reused here almost exactly:

- `detect_bias()` / `detect_toxicity()` are pure pattern-matchers applied to a
  bare string — the notebook calls them on the user's question (`evaluate_input`)
  AND on the generated answer (`evaluate_output`), same functions both times.
  Flight Agent does the same: `screen_input_text()` on `trip_context.preferences`
  before the LLM ever sees them, `screen_output_text()` on the LLM's rationale
  before it's trusted. Symmetric by design, not two different implementations.
- Bias risk is two-tier, not a single flag: a bare protected-attribute mention
  ("Chinese New Year travel", "near a mosque") is `risk_level="medium"` and NOT
  blocked — only a protected-attribute mention COMBINED with a stereotype-trigger
  word (all/always/never/inferior/"better at"/etc.) is `risk_level="high"` and
  blocked. This avoids false-positive blocks on ordinary travel content while
  still catching "X people are naturally worse at flying" style text. Adopted
  verbatim from the notebook's `detect_bias()` — it's the right shape for this
  domain too (destinations/cuisines/neighbourhoods legitimately mention
  nationality/religion/culture; only stereotyping is the actual problem).
- Toxicity uses the notebook's `keyword_fallback` backend, not `detoxify` — that
  library is a real ML model dependency (transformers-based) not worth adding
  for a keyword-fallback-equivalent result; the notebook itself treats
  keyword_fallback as an acceptable degraded mode, not a lesser design.

One adaptation, not a verbatim copy — documented so it doesn't misrepresent the
source: the notebook's injection pattern `ignore (all|any|the) previous
instructions` requires a determiner between "ignore" and "previous instructions".
Common injection phrasing often omits it ("ignore previous instructions"), which
the existing test suite already covers — made the determiner optional
(`(all |any |the )?`) rather than dropping the pattern, so it still catches
the two more specific notebook variants without missing the bare one.

Fail-closed by construction: any INPUT violation (injection, high bias risk,
toxicity) short-circuits `run_flight_agent` before the LLM is ever called —
poisoned input never reaches the model, not even to be "handled." An OUTPUT
violation is treated the same as a grounding failure: retry once, then fall
back to the deterministic response.
"""

from __future__ import annotations

import re
from typing import Any

from flaskapp.travel_ai.agents.flight_agent.schemas import FlightProposal

# --- Prompt injection (adapted from the XRAI notebook's YAML, see module docstring) ---

INJECTION_PATTERNS = [
    r"(?i)\bignore (all |any |the )?previous instructions\b",
    r"(?i)\b(disable|bypass|override|evade) (the )?(guardrails|policy|governance|controls)\b",
    r"(?i)\b(show|reveal|print|dump) (the )?(system prompt|hidden prompt|policy secrets)\b",
    r"(?i)\bact as (an? )?(unrestricted|unfiltered|developer mode)\b",
]


def screen_preferences(preferences: list[str]) -> list[str]:
    """Pre-tool gate: which free-text preference entries look instruction-like.

    Returns the offending entries (empty list = clean). Flight Agent's only
    free-text input is `trip_context.preferences` — everything else is
    structured (dates, airports, numbers) and can't carry a prompt injection.
    """
    return [text for text in preferences if any(re.search(p, text) for p in INJECTION_PATTERNS)]


# --- Bias (protected-attribute mention + stereotype trigger = high risk) ---

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
    """Two-tier bias risk over a single string — see module docstring for why
    "medium" (attribute alone) is deliberately not the same as "high"
    (attribute + stereotyping) and only "high" blocks anything."""
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


# --- Toxicity (keyword fallback — see module docstring on why not detoxify) ---

TOXICITY_TERMS: dict[str, list[str]] = {
    "toxicity": ["stupid", "idiot", "hate", "trash", "useless"],
    "severe_toxicity": ["kill", "destroy them", "die"],
    "insult": ["idiot", "stupid", "dumb", "moron"],
    "identity_attack": ["all women", "all men", "all asians", "all foreigners", "all muslims", "all christians"],
}


def detect_toxicity(text: str) -> dict[str, Any]:
    lower = text.lower()
    flagged_labels = sorted(label for label, terms in TOXICITY_TERMS.items() if any(term in lower for term in terms))
    return {"flagged": bool(flagged_labels), "labels": flagged_labels}


# --- Symmetric input/output screening (the same two detectors, both directions) ---


def _text_values(preferences: dict | list | None) -> list[str]:
    """Every free-text string inside `trip_context.preferences`, whichever
    shape it arrives in.

    The intake form (`app.js`'s `buildPayload`) comma-splits its preferences
    textarea into a LIST of strings; earlier drafts of this module assumed a
    free-form DICT (area, star_min, red-eye ok, ...) whose string-valued
    entries were the screenable part. Both are accepted rather than forcing
    one on the other, because the producer is not Flight Agent's to dictate.
    Non-string entries (numbers, booleans) can't carry text and are skipped.
    """
    if preferences is None:
        return []
    values = preferences.values() if isinstance(preferences, dict) else preferences
    return [v for v in values if isinstance(v, str)]


def screen_input_text(preferences: dict | list | None, *extra_texts: list[str]) -> dict[str, Any]:
    """Pre-tool gate over ALL of the traveller's free text at once — injection,
    bias, toxicity together. `blocked=True` means: do not call the LLM with
    this input at all.

    `extra_texts` takes any further free-text lists that must clear the same
    gate — currently `trip_context.refinement_notes`, which reaches the agent
    through the "Refine your plan" panel and is exactly as untrusted as the
    original preferences were.
    """
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
    """Post-tool gate over the LLM's own rationale text — same two detectors
    as the input side, applied to what the model said instead of what it was
    given. `flagged=True` means: don't trust this response, retry/fall back."""
    bias = detect_bias(text)
    toxicity = detect_toxicity(text)
    flagged = bias["risk_level"] == "high" or toxicity["flagged"]
    return {"flagged": flagged, "bias": bias, "toxicity": toxicity}


# --- Grounding (unchanged) ---


def validate_grounded_explanation(mentioned_flight_ids: list[str], proposal: FlightProposal) -> list[str]:
    """Post-tool grounding gate for the explanation-generation LLM call.

    An LLM asked to explain a proposal in natural language could still name a
    flight_id that isn't actually in the proposal it was given — this is the
    check that catches that before the explanation reaches a user. Returns
    the offending ids (empty list = every claim is grounded).

    Implements llmops_plan.md §3's "grounding by construction" concretely:
    verify every mentioned id exists in what the agent actually returned.
    """
    known_ids = {candidate.flight_id for candidate in proposal.candidates}
    return [flight_id for flight_id in mentioned_flight_ids if flight_id not in known_ids]
