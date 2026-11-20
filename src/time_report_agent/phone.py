"""Swedish phone normalization to E.164."""

from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException


def normalize_e164(raw: str, *, region: str = "SE") -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = phonenumbers.parse(text, region)
    except NumberParseException:
        return None
    if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def redact_phone(e164_or_raw: str | None) -> str:
    """Return a short safe token for logs/stdout."""
    if not e164_or_raw:
        return "<no-phone>"
    digits = "".join(ch for ch in e164_or_raw if ch.isdigit())
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"
