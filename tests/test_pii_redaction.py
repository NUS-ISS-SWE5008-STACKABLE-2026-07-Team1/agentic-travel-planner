"""L1 PII redaction on the intake prompt.

The date tests are the reason this layer is ordered rather than a dict: the
reference `phone` pattern matches `2026-10-10`, and departure/return dates are
the two fields intake exists to extract.
"""

import pytest

from flaskapp.travel_ai.guardrails.pii import PiiRedactor


@pytest.fixture
def redactor():
    return PiiRedactor()


def test_email_is_redacted(redactor):
    result = redactor.redact("write to me at jane.doe@example.com please")
    assert result.text == "write to me at [REDACTED_EMAIL] please"


def test_nric_is_redacted(redactor):
    assert redactor.redact("my NRIC is S1234567D").text == "my NRIC is [REDACTED_NRIC]"


def test_passport_is_redacted(redactor):
    assert redactor.redact("passport E12345678 issued").text == (
        "passport [REDACTED_PASSPORT] issued"
    )


def test_phone_is_redacted(redactor):
    assert redactor.redact("call me on +65 9123 4567").text == "call me on [REDACTED_PHONE]"


def test_iso_dates_are_not_mistaken_for_phone_numbers(redactor):
    """The regression the `phone` guard exists for.

    The reference pattern matches `2026-10-10`. Departure and return dates are
    the two fields intake exists to extract, so redacting them would silently
    destroy every trip request that states its dates numerically.
    """
    prompt = "flying 2026-10-10 returning 2026-10-16"
    result = redactor.redact(prompt)
    assert result.text == prompt
    assert result.counts == {}


def test_a_real_phone_survives_alongside_a_date(redactor):
    result = redactor.redact("call +65 9123 4567 about 2026-10-10")
    assert result.text == "call [REDACTED_PHONE] about 2026-10-10"


def test_credit_card_is_masked_keeping_the_last_four(redactor):
    result = redactor.redact("card 4111 1111 1111 1111 expires soon")
    assert result.text == "card **** **** **** 1111 expires soon"


def test_credit_card_is_labelled_a_card_not_a_phone(redactor):
    """`phone` matches a card number in full, so rule order decides the label."""
    result = redactor.redact("card 4111 1111 1111 1111")
    assert result.counts == {"credit_card": 1}


def test_a_grouped_number_is_treated_as_a_card_whatever_the_words_around_it(redactor):
    """The deliberate trade-off in preferring shape over the checksum.

    A booking reference written in 4-4-4-4 grouping gets masked as a card. That
    is accepted: nothing else in travel prose is written that way, and masking a
    reference costs the traveller nothing, while mislabelling a real card costs
    the audit trail its accuracy.
    """
    result = redactor.redact("reference 1234 5678 9012 3456 attached")
    assert result.counts == {"credit_card": 1}


def test_counts_report_each_rule_and_carry_no_matched_text(redactor):
    result = redactor.redact("jane@example.com, S1234567D, +65 9123 4567")
    assert result.counts == {"email": 1, "nric": 1, "phone": 1}
    assert result.redacted is True
    assert result.as_audit_details() == {
        "rules": {"email": 1, "nric": 1, "phone": 1}, "total": 3
    }


def test_clean_text_is_returned_unchanged(redactor):
    prompt = "Tokyo for two weeks in October on a 3000 SGD budget"
    result = redactor.redact(prompt)
    assert result.text == prompt
    assert result.redacted is False


def test_disabled_redactor_is_an_exact_no_op():
    prompt = "my NRIC is S1234567D and my card is 4111 1111 1111 1111"
    result = PiiRedactor(enabled=False).redact(prompt)
    assert result.text == prompt
    assert result.counts == {}


def test_from_config_reads_the_enabled_flag():
    prompt = "my NRIC is S1234567D"
    assert PiiRedactor.from_config({"PII_REDACTION_ENABLED": False}).redact(prompt).text == prompt
    assert PiiRedactor.from_config({"PII_REDACTION_ENABLED": True}).redact(prompt).redacted


def test_a_grouped_card_is_masked_even_when_the_checksum_fails(redactor):
    """Reported from a live run: a 4-4-4-4 number that fails Luhn was labelled a phone.

    A typo'd or test card is still a card. Shape decides what it IS; the Luhn
    check only decides whether an UNGROUPED digit run is a card or a reference
    number. Mislabelling it left the audit trail saying "phone" when a card had
    been redacted.
    """
    result = redactor.redact("I use my credit card 2342 1234 2323 2323 for memberships")
    assert result.counts == {"credit_card": 1}
    assert result.text == "I use my credit card **** **** **** 2323 for memberships"


def test_an_ungrouped_digit_run_still_needs_the_checksum(redactor):
    """The Luhn guard still earns its place: this is a reference, not a card."""
    result = redactor.redact("booking reference 1234567890123456 attached")
    assert "credit_card" not in result.counts
