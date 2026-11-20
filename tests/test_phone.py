from time_report_agent.phone import normalize_e164, redact_phone


def test_normalize_plus46() -> None:
    assert normalize_e164("+46701234567") == "+46701234567"


def test_normalize_leading_zero() -> None:
    assert normalize_e164("0701234567") == "+46701234567"


def test_normalize_46_without_plus() -> None:
    assert normalize_e164("46701234567") == "+46701234567"


def test_normalize_rejects_short() -> None:
    assert normalize_e164("123") is None
    assert normalize_e164("") is None


def test_redact_phone_hides_digits() -> None:
    token = redact_phone("+46701234567")
    assert "070" not in token
    assert "7012" not in token
    assert token.endswith("4567")
