"""Risk & Advisory Agent's own guardrails — written independently, not forked
from another agent's.

Two concerns, kept in separate sections below because they catch different
failure modes:

1. **Content screening** (injection / bias / toxicity) on the traveller's own
   free text before the model sees it, and on the model's generated text
   before it is trusted. This agent's subject matter — visa law, personal
   safety, and a protected group's legal status in a given country — makes
   the bias line genuinely harder to draw than "does this preferences string
   mention a religion": stating that a country's law criminalises a
   protected group is the correct, sourced content this agent exists to
   produce; generalising about the people of that country is not. The
   two-tier shape (attribute mention alone is not blocking; attribute +
   generalising language is) is the same reasoning already validated
   elsewhere in this project's guardrails, applied here to this agent's own
   domain and its own word lists — not the same code.

2. **Grounding and escalation** against `domain.py`'s actual output — this is
   specific to this agent and has no equivalent anywhere else: no other
   agent's guardrails check "did the model cite a fact that doesn't exist"
   or "does the data say this should have escalated and the model didn't."
"""

from __future__ import annotations

import re
from typing import Any

from flaskapp.travel_ai.agents.risk_advisory_agent.schemas import RiskAgentResponse, RiskProposal

# --- 1a. Prompt injection --------------------------------------------------

_INJECTION_RULES: dict[str, str] = {
    "override_instructions": r"\b(ignore|disregard|forget)\b.{0,20}\b(previous|prior|above|earlier)\b.{0,20}\b(instructions?|rules?|prompt)\b",
    "reveal_system": r"\b(reveal|show|print|repeat|output)\b.{0,20}\b(system prompt|hidden prompt|instructions?)\b",
    "assume_persona": r"\byou are now\b|\bact as (an? )?(unrestricted|unfiltered|jailbroken|dan)\b|\bpretend (you are|to be)\b",
    "authority_override": r"\bas an? (admin|developer|system) (user|operator)?\b.{0,20}\b(override|bypass)\b",
    "fabricate_live_fact": (
        r"\b(assume|pretend|treat)\b.{0,20}\b(border|visa|advisory|entry)\b.{0,20}"
        r"\b(is|are|has been)\b.{0,20}\b(open|closed|lifted|waived)\b"
    ),
}
_INJECTION_PATTERNS = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in _INJECTION_RULES.items()}


def detect_injection(text: str) -> str | None:
    """The name of the first injection rule that fires, or None.

    `fabricate_live_fact` is specific to this agent: it exists nowhere else
    because no other specialist is asked to state real-world regulatory
    facts it cannot verify. A traveller (or a compromised upstream field)
    instructing the model to simply assume a border is open is exactly the
    failure this agent's grounding exists to prevent, and it is worth
    catching before the model call, not only after.
    """
    if not isinstance(text, str) or not text:
        return None
    for name, pattern in _INJECTION_PATTERNS.items():
        if pattern.search(text):
            return name
    return None


# --- 1b. Bias — two-tier: an attribute mention alone is not the problem ---

_PROTECTED_ATTRIBUTE_TERMS: dict[str, list[str]] = {
    "nationality": [r"\b(nationality|nationals?|citizens?|foreigners?)\b"],
    "religion": [r"\b(muslims?|christians?|jews?|jewish|hindus?|buddhists?|religion|faith)\b"],
    "gender_or_orientation": [
        r"\b(women|men|female|male|lgbtq\+?|gay|lesbian|transgender|non[- ]?binary|same[- ]sex)\b"
    ],
    "disability": [r"\b(disab(led|ility)|wheelchair users?|blind|deaf)\b"],
    "age": [r"\b(elderly|older travellers?|young travellers?|children|minors?)\b"],
}
_GENERALISING_TERMS = [
    r"\b(all|every|none|never|always)\b",
    r"\b(inferior|superior|dangerous|untrustworthy|criminal(?:s)?)\b",
    r"\b(should not|shouldn'?t|cannot|can'?t|avoid) (be|be allowed|travel|visit)\b",
]
_ATTRIBUTE_PATTERNS = {
    name: [re.compile(p, re.IGNORECASE) for p in patterns] for name, patterns in _PROTECTED_ATTRIBUTE_TERMS.items()
}
_GENERALISING_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _GENERALISING_TERMS]


def detect_bias(text: str) -> dict[str, Any]:
    """Two-tier: attribute alone -> "medium", not blocking. Attribute plus a
    generalising claim -> "high", blocking.

    Stating "this country's law criminalises same-sex relationships" mentions
    an attribute (gender_or_orientation) with no generalising language, so it
    stays "medium" — exactly the traveler_group_risk content this agent is
    meant to produce, sourced from `seed_data.py`. "Gay travellers are not
    welcome here" mentions the same attribute plus a should-not/cannot claim,
    so it is "high" and blocked — that is no longer a legal fact, it is a
    generalisation the model produced.
    """
    if not isinstance(text, str):
        text = ""
    attributes = sorted(
        name for name, patterns in _ATTRIBUTE_PATTERNS.items() if any(p.search(text) for p in patterns)
    )
    generalising = any(p.search(text) for p in _GENERALISING_PATTERNS)
    if attributes and generalising:
        level = "high"
    elif attributes:
        level = "medium"
    else:
        level = "low"
    return {"risk_level": level, "attributes_detected": attributes, "generalising_language": generalising}


# --- 1c. Toxicity — word-bounded, own short list --------------------------

_TOXIC_TERMS = ("idiot", "stupid", "worthless", "hate", "trash")
_TOXIC_PATTERNS = [re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in _TOXIC_TERMS]


def detect_toxicity(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return any(pattern.search(text) for pattern in _TOXIC_PATTERNS)


# --- 1d. Combined input/output screening -----------------------------------

def screen_input(texts: list[str]) -> dict[str, Any]:
    """Pre-model gate over the traveller's own free text.

    `blocked=True` means: do not call the model with this text at all.
    """
    injection_hits = [(t, name) for t in texts if (name := detect_injection(t))]
    bias_hits = [(t, detect_bias(t)) for t in texts]
    high_bias = [(t, b) for t, b in bias_hits if b["risk_level"] == "high"]
    toxic_hits = [t for t in texts if detect_toxicity(t)]
    return {
        "blocked": bool(injection_hits or high_bias or toxic_hits),
        "injection": injection_hits,
        "high_bias": high_bias,
        "toxicity": toxic_hits,
    }


def screen_output(text: str) -> dict[str, Any]:
    """Post-model gate over the generated rationale. `flagged=True` means:
    do not trust this response, retry or fall back."""
    injection = detect_injection(text)
    bias = detect_bias(text)
    toxic = detect_toxicity(text)
    return {
        "flagged": bool(injection) or bias["risk_level"] == "high" or toxic,
        "injection": injection,
        "bias": bias,
        "toxicity": toxic,
    }


# --- 2. Grounding and escalation, against domain.py's actual output -------

def validate_grounded_response(response: RiskAgentResponse, proposal: RiskProposal) -> list[str]:
    """Every `risk_id` the model cites must exist in what `propose_risks()`
    actually returned. Anything else is a fabricated citation — the model
    stating a fact `domain.py` never produced. Returns the offending ids."""
    known_ids = {item.risk_id for item in proposal.items}
    return [risk_id for risk_id in response.highlighted_risk_ids if risk_id not in known_ids]


def expected_escalation_reasons(proposal: RiskProposal) -> list[str]:
    """Why this proposal should produce `escalate=True`, per the data alone —
    never per the model's own wording. Empty means nothing in the data
    requires it."""
    return [
        f"{item.category} item '{item.title}' is high-severity"
        for item in proposal.items if item.severity == "high"
    ]


def missing_escalation(response: RiskAgentResponse, proposal: RiskProposal) -> list[str]:
    """Data-backed reasons the response should have escalated but didn't.
    Empty = clean: either nothing required it, or the response already has
    `escalate=True`."""
    reasons = expected_escalation_reasons(proposal)
    if not reasons or response.escalate:
        return []
    return reasons
