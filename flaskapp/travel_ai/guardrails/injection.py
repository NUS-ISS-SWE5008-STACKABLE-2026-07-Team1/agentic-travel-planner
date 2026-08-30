"""L1: prompt-injection detection.

Extracted from `safeguards.py`, where it was one regex of four alternatives, and
broadened. `classifier.py`'s docstring is right that L1 cannot see an attack
encoded in base64 or split across whitespace, and that L2 exists for exactly
that — but this is not an argument for leaving L1 small. Every attack L1 catches
costs no API call, cannot be talked out of its instructions by the text it is
judging, and cannot fail open when a provider times out. L2 is the backstop, not
the replacement.

`detect` returns the name of the rule that fired rather than raising:
`safeguards` already imports this package, and raising from inside it would need
the reverse edge and make a cycle. The raise stays with the caller.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class InjectionRule:
    """One named family of injection phrasings.

    `source` carries its own scoped inline flag rather than relying on a
    compile-time one, because the rules do not agree about case:
    `encoded_payload` distinguishes upper from lower to identify base64, and a
    global `re.IGNORECASE` would erase that distinction.
    """

    name: str
    source: str

    @property
    def pattern(self) -> re.Pattern[str]:
        return re.compile(self.source)


# Case-insensitive, scoped per rule with `(?i:...)`.
_TEXT_RULES: tuple[tuple[str, str], ...] = (
    ("instruction_override",
     r"ignore (all|any|previous)|disregard (the )?(previous|above|prior)"
     r"|forget (everything|all|previous)"),
    ("system_prompt",
     r"system prompt|developer message|reveal .*prompt|initial instructions"),
    # `act as` requires an adversarial complement. Bare "act as" was one of the
    # original four alternatives and it denied "act as our guide", which is
    # ordinary phrasing for this product. The per-agent detectors were tightened
    # the same way long ago; this brings the HTTP boundary into line with them
    # rather than leaving the two disagreeing.
    ("role_play",
     r"act as (an?\s+)?(unrestricted|unfiltered|uncensored|jailbroken|admin|administrator"
     r"|developer|system|dan\b)"
     r"|act as if you (are|were)"
     r"|you are now|pretend (to be|you are)|roleplay as|DAN mode"),
    ("delimiter",
     r"</?(system|instructions?|admin)>|\[/?(INST|SYS)\]|###\s*(system|instruction)"),
    ("tool_instruction",
     r"call (the )?tool|execute (the )?(command|code)|run the following"),
)

# Case-SENSITIVE. The three lookaheads require a digit and both cases inside the
# run, which is what keeps this rule off natural language: travel prose does not
# contain 40-character unbroken mixed-case alphanumeric tokens, and requiring the
# mix rules out a long single-case word.
_ENCODED_PAYLOAD = (
    r"(?i:base64:)"
    r"|(?=[A-Za-z0-9+/]*[a-z])(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*\d)"
    r"[A-Za-z0-9+/]{40,}={0,2}"
)

RULES: tuple[InjectionRule, ...] = (
    *(InjectionRule(name, f"(?i:{source})") for name, source in _TEXT_RULES),
    InjectionRule("encoded_payload", _ENCODED_PAYLOAD),
)

# The union of every rule, kept under the name `safeguards` has always exported.
# Three readers depend on it: `validate_request`, the admin prompt catalog
# (which renders `.pattern`), and the eval harness's L1 attribution.
PROMPT_INJECTION: re.Pattern[str] = re.compile(
    "|".join(f"(?:{rule.source})" for rule in RULES)
)


class PromptInjectionGuard:
    """Reports which rule a piece of text trips, in declared order."""

    def __init__(self, rules: Sequence[InjectionRule] = RULES):
        self._rules = tuple((rule, rule.pattern) for rule in rules)

    def detect(self, text: object) -> str | None:
        """The name of the first rule that matches, or None."""
        if not isinstance(text, str) or not text:
            return None
        for rule, pattern in self._rules:
            if pattern.search(text):
                return rule.name
        return None
