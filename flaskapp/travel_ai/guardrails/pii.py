"""L1: deterministic PII redaction.

Sits between the injection guard and the L2 classifier in `screen_prompt`, and
that position is the whole design. L2 is a network call to a model provider; a
redaction step that ran after it would mean the one component whose job is to
notice PII is also the component that transmits it. Redaction is local regex and
sub-millisecond, so there is no cost argument for the other order.

Unlike every other guardrail layer, this one never blocks. A traveller who
volunteers a phone number is not an attacker, and a 422 teaches them nothing
except that the product is broken. The identifier is removed and planning
continues; the trip does not need it. L2's `pii_exposure` category remains the
semantic backstop for the adversarial case.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Strategy = Literal["redact", "mask"]


@dataclass(frozen=True)
class PiiRule:
    """One identifier shape, and what to do with it.

    `guard` returns True to SKIP a match. It reads `match.string`, the live text
    as of this rule, so it stays correct after earlier rules have rewritten it.
    """

    name: str
    pattern: re.Pattern[str]
    strategy: Strategy = "redact"
    guard: Callable[[re.Match[str]], bool] | None = None


@dataclass(frozen=True)
class RedactionResult:
    """Redacted text, plus how many of each rule fired — never what matched."""

    text: str
    counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def redacted(self) -> bool:
        return bool(self.counts)

    def as_audit_details(self) -> dict:
        """Trace-safe projection: rule names and integers only.

        Modelled on `Verdict.as_audit_details` and for the same reason. This
        value reaches the trace file, which `GET /api/v1/traces/<id>` serves and
        the admin dashboard renders. Recording *what* was redacted would put the
        identifier back into the audit log this layer exists to keep it out of.
        """
        return {
            "rules": {name: count for name, count in sorted(self.counts.items())},
            "total": sum(self.counts.values()),
        }


EMAIL = re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
NRIC = re.compile(r"\b[STFG]\d{7}[A-Z]\b")
PASSPORT = re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")
# `(?<!\w)` rather than the more obvious `\b` before the optional `+`: a word
# boundary cannot hold between a space and a `+` (neither is a word character),
# so `\b\+?` silently leaves the country-code `+` behind in the redacted text.
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s\-().]{7,}\d\b")

# `nric` precedes `passport` defensively. They were measured not to collide —
# PASSPORT will not match inside `S1234567D`, because the trailing letter denies
# the closing word boundary — but the ordering costs nothing, and the narrower
# rule winning is the property we want if either pattern is ever widened.
CREDIT_CARD = re.compile(r"\b(?:\d[ -]*?){13,16}\b")

# Dates written numerically, which PHONE matches in full. See `_within_iso_date`.
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


# A card written in the conventional four-by-four grouping. Shape alone is
# enough here: nothing else in travel prose is written this way.
GROUPED_CARD = re.compile(r"\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}")


def _luhn(matched: str) -> bool:
    """Whether a digit run passes the check digit every card number carries.

    Travel prose is full of long numbers — budgets, references, durations — and
    an UNGROUPED 13-16 digit run that fails Luhn is not a card. Masking it would
    corrupt the request for no gain.

    Not sufficient on its own, though — see `_is_card`.
    """
    digits = [int(character) for character in matched if character.isdigit()]
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _is_card(matched: str) -> bool:
    """Either the checksum passes, or it is written in card shape.

    Luhn alone was wrong, and a live run proved it: a traveller typed a card in
    4-4-4-4 grouping whose checksum failed — a typo or a test number — so the
    card rule skipped it and `phone` claimed the span. The number was still
    redacted, but the audit trail recorded "phone" for a card.

    A mistyped card is still a card. Shape decides what a grouped number is;
    Luhn is only needed to tell an ungrouped digit run from a booking reference.
    """
    return _luhn(matched) or bool(GROUPED_CARD.fullmatch(matched.strip()))


def _within_iso_date(match: re.Match[str]) -> bool:
    """True when this match is, or sits inside, a numeric date.

    Reads `match.string` — the live text as of this rule — so it stays correct
    after earlier rules have already rewritten parts of the prompt.
    """
    start, end = match.span()
    return any(
        date.start() < end and start < date.end()
        for date in ISO_DATE.finditer(match.string)
    )


# Order is load-bearing. Two overlaps were measured against the reference
# patterns, and whichever rule runs first owns the span:
#
#   credit_card before phone   PHONE matches `4111 1111 1111 1111` in full, so
#                              a card left to phone would be labelled a phone.
#   nric before passport       Defensive only. PASSPORT will not match inside
#                              `S1234567D` — the trailing letter denies the
#                              closing word boundary — but the narrower rule
#                              winning is what we want if either is widened.
#
# A card that fails Luhn falls through to `phone` and is redacted rather than
# masked. Over-redaction is the safe direction to err.
DEFAULT_RULES: tuple[PiiRule, ...] = (
    PiiRule("credit_card", CREDIT_CARD, "mask", guard=lambda m: not _is_card(m.group(0))),
    PiiRule("email", EMAIL),
    PiiRule("nric", NRIC),
    PiiRule("passport", PASSPORT),
    PiiRule("phone", PHONE, guard=_within_iso_date),
)


def _redact(matched: str, rule: PiiRule) -> str:
    return f"[REDACTED_{rule.name.upper()}]"


def _mask(matched: str, rule: PiiRule) -> str:
    """Star every digit but the last four, keeping the original separators.

    Digit count and formatting survive, so a masked value is still recognisable
    as what it was in a transcript without being usable as it.
    """
    keep = 4
    digits_total = sum(character.isdigit() for character in matched)
    seen = 0
    out = []
    for character in matched:
        if not character.isdigit():
            out.append(character)
            continue
        seen += 1
        out.append(character if seen > digits_total - keep else "*")
    return "".join(out)


_STRATEGIES: dict[str, Callable[[str, PiiRule], str]] = {"redact": _redact, "mask": _mask}


class PiiRedactor:
    """Applies an ordered rule table to one piece of traveller text.

    Order is load-bearing, not incidental: the rules overlap, and whichever
    matches first owns the span. Construct with `from_config` so the enabled
    flag comes from one place.
    """

    def __init__(
        self, rules: Sequence[PiiRule] = DEFAULT_RULES, *, enabled: bool = True
    ):
        self._rules = tuple(rules)
        self._enabled = enabled

    def redact(self, text: Any) -> RedactionResult:
        """Never raises. An input this cannot handle is returned unchanged."""
        if not self._enabled or not isinstance(text, str) or not text:
            return RedactionResult(text=text, counts={})
        counts: Counter[str] = Counter()
        for rule in self._rules:
            apply = _STRATEGIES[rule.strategy]

            def replace(match: re.Match[str], rule: PiiRule = rule, apply=apply) -> str:
                if rule.guard is not None and rule.guard(match):
                    return match.group(0)
                counts[rule.name] += 1
                return apply(match.group(0), rule)

            text = rule.pattern.sub(replace, text)
        return RedactionResult(text=text, counts=dict(counts))

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None = None) -> PiiRedactor:
        """Build from application config.

        Mirrors the `vars(Config) if config is None else config` seam
        `flight_agent/agent.py:190` established, so tests override the flag with
        a plain dict and never need environment variables.
        """
        if config is None:
            from flaskapp.config import Config

            config = vars(Config)
        return cls(enabled=bool(config.get("PII_REDACTION_ENABLED", True)))
