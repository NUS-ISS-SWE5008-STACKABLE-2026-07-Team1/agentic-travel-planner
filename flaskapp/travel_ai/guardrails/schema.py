"""The contract the L2 classifier is constrained to.

Every call goes through `with_structured_output(GuardrailVerdict,
method="json_schema")`, matching the house pattern (`agents/base.py:32`) and the
Semgrep rule `llm-invoke-without-structured-output`. A response that does not
parse to this shape is a guardrail failure, handled by the configured fail mode.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Bump when the classifier prompts change. It is part of the cache key, so a
# prompt edit invalidates cached verdicts instead of silently reusing verdicts
# produced by different instructions, and it is stamped into the eval report so
# a set of metrics is always attributable to the prompt that produced it.
GUARDRAIL_PROMPT_VERSION = "l2-2026-08-16b"


class GuardrailVerdict(BaseModel):
    """Classifier output. Only `decision` and `category` are ever acted on."""

    decision: Literal["allow", "flag", "block"]
    category: Literal[
        "none",
        "prompt_injection",
        "jailbreak",
        "out_of_scope",
        "harmful_request",
        "bias_stereotyping",
        "pii_exposure",
        "system_prompt_leak",
        "ungrounded_claim",
        "other",
    ]
    # Range is stated in the description rather than enforced with ge/le on
    # purpose. A model that answers 1.2 would otherwise raise ValidationError,
    # and under fail-closed that turns a cosmetic formatting slip into a denied
    # request for a legitimate traveller. The value is clamped in the classifier
    # instead, where an out-of-range number costs nothing.
    confidence: float = Field(
        description="How certain the judgement is, from 0.0 to 1.0."
    )
    # Unbounded here and truncated in the classifier, for the same reason.
    rationale: str = Field(
        default="", description="One short sentence. Fewer than 200 characters."
    )
