"""Text normalization that runs ahead of any regex-based detector.

`guardrails/prompts.py`'s L2 classifier instructions already name the attack
class this closes: injection "smuggled through encoding (base64, leetspeak,
homoglyphs, spaced-out letters...)". Base64 got a real detector
(`guardrails/injection.py`'s `encoded_payload` rule). Homoglyph substitution
and character-splitting did not — nothing in this codebase normalizes text
before a regex sees it, so `"іgnore previous instructions"` (a Cyrillic
`і` standing in for the Latin `i`) does not contain the substring `ignore` and
every regex-based detector — L1 injection, bias, toxicity alike — simply does
not match it.

This module is a pre-processing step, not a detector of its own: normalize
first, then run the existing regex detectors on the normalized text. It
raises nothing and blocks nothing by itself.
"""

from __future__ import annotations

import unicodedata

# Characters visually confusable with a Latin letter, mapped to that letter.
# Deliberately small and conservative — covers the Cyrillic and Greek
# look-alikes an attacker can type from a standard input method, not an
# exhaustive Unicode confusables table. A wrong substitution in ordinary
# multilingual travel content (a city name, a traveller's own script) is a
# false positive; the letters below have no plausible non-adversarial use
# inside an otherwise-English instruction-shaped sentence.
CONFUSABLE_MAP: dict[str, str] = {
    # Cyrillic
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "У": "Y",
    "і": "i", "І": "I", "ѕ": "s",
    # Greek
    "α": "a", "ο": "o", "ρ": "p", "υ": "y", "ν": "v", "ι": "i",
}

# Zero-width and other invisible characters used to split a flagged word
# across characters a regex would otherwise match contiguously (e.g.
# "ig​nore"). Stripped outright — none of them render, so removing them
# changes nothing a traveller would see.
_ZERO_WIDTH = (
    "​"  # zero-width space
    "‌"  # zero-width non-joiner
    "‍"  # zero-width joiner
    "﻿"  # zero-width no-break space / BOM
    "⁠"  # word joiner
)
_ZERO_WIDTH_TABLE = str.maketrans("", "", _ZERO_WIDTH)


def normalize_for_screening(text: str) -> str:
    """The text a detector should actually run its regexes against.

    Order: Unicode compatibility fold first (NFKC — collapses fullwidth and
    other compatibility forms into their ordinary equivalents), then strip
    invisible characters, then substitute known confusables. Returns the
    original input unchanged on any non-string input rather than raising —
    callers already handle "not a string" as "nothing to screen".
    """
    if not isinstance(text, str) or not text:
        return text
    folded = unicodedata.normalize("NFKC", text)
    stripped = folded.translate(_ZERO_WIDTH_TABLE)
    return "".join(CONFUSABLE_MAP.get(ch, ch) for ch in stripped)
