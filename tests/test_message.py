from time_report_agent.message import build_reminder_sms, first_name


def test_first_name() -> None:
    assert first_name("Synthetic Person") == "Synthetic"


def test_build_reminder_uses_csv_hours_and_name() -> None:
    text = build_reminder_sms(
        employee_name="Synthetic Person",
        missing_hours=12.5,
        period="2026-11",
        organization="Acme",
    )
    assert text.startswith("Hej Synthetic!")
    assert "12.5" in text
    assert "2026-11" in text
    assert text.endswith("/Acme")
