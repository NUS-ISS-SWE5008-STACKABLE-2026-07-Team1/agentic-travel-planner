"""Result types shared by every guardrail layer.

L0 (pydantic schemas), L1 (regex/keyword detectors) and L2 (the LLM classifier)
all reduce to the same `Verdict`, so a caller can treat them uniformly and the
eval harness can attribute a block to the layer that actually caught it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    """What the pipeline does with the text.

    FLAG is deliberately distinct from BLOCK: it records a suspicion in the
    audit trail without denying the traveller, which is what a low-confidence
    L2 verdict earns. Only BLOCK stops the request.
    """

    ALLOW = "allow"
    FLAG = "flag"
    BLOCK = "block"


class Category(str, Enum):
    """Why the text was judged. Consumed as an enum, never as free text."""

    NONE = "none"
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    OUT_OF_SCOPE = "out_of_scope"
    HARMFUL_REQUEST = "harmful_request"
    BIAS_STEREOTYPING = "bias_stereotyping"
    PII_EXPOSURE = "pii_exposure"
    SYSTEM_PROMPT_LEAK = "system_prompt_leak"
    UNGROUNDED_CLAIM = "ungrounded_claim"
    OTHER = "other"


@dataclass(frozen=True)
class Verdict:
    """One layer's judgement on one piece of text."""

    decision: Decision
    category: Category = Category.NONE
    confidence: float = 0.0
    layer: str = "L2"
    latency_ms: int = 0
    rationale: str = ""
    cache_hit: bool = False

    @property
    def blocked(self) -> bool:
        return self.decision is Decision.BLOCK

    def as_audit_details(self) -> dict:
        """Trace-safe projection: enums and numbers only.

        `rationale` is deliberately excluded. It is model-generated text derived
        from attacker-controlled input, and the trace is served by
        `GET /api/v1/traces/<id>` and rendered in the admin dashboard — echoing
        it there would reintroduce, in the audit log, exactly the untrusted
        content this layer exists to stop. It stays on the Verdict for local
        debugging and for the offline eval report, both of which are read by a
        developer rather than rendered in a page.
        """
        return {
            "decision": self.decision.value,
            "category": self.category.value,
            "confidence": round(self.confidence, 3),
            "layer": self.layer,
            "latency_ms": self.latency_ms,
            "cache_hit": self.cache_hit,
        }


ALLOWED = Verdict(decision=Decision.ALLOW, layer="L2")
