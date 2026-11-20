from __future__ import annotations

from datetime import date

from time_report_agent.csv_queue import LocalDisposition, QueueRow
from time_report_agent.eligibility import (
    Outcome,
    SmsStatusView,
    apply_sms_guards,
    classify_local,
)


def _valid_row(*, missing_hours: float | None = 8.0) -> QueueRow:
    return QueueRow(
        row_number=2,
        employee_id="emp-1",
        employee_name="Synthetic Person",
        telephone_raw="0701234567",
        telephone_e164="+46701234567",
        entry_date=date(2026, 11, 5),
        hours_raw="" if missing_hours is None else str(missing_hours),
        missing_hours=missing_hours,
        disposition=LocalDisposition.VALID,
    )


def test_csv_fields_make_eligible() -> None:
    candidate = classify_local(
        _valid_row(missing_hours=8.0),
        period="2026-11",
        organization="tenant-a",
    )
    assert candidate.outcome is Outcome.ELIGIBLE
    assert candidate.missing_hours == 8.0
    assert candidate.sms_preview is not None
    assert "8" in candidate.sms_preview
    assert "Synthetic" in candidate.sms_preview
    assert "2026-11" in candidate.sms_preview


def test_blank_hours_still_eligible_with_preview() -> None:
    candidate = classify_local(
        _valid_row(missing_hours=None),
        period="2026-11",
        organization="tenant-a",
    )
    assert candidate.outcome is Outcome.ELIGIBLE
    assert "okända" in (candidate.sms_preview or "")


def test_suppressed_skipped() -> None:
    result = apply_sms_guards(
        classify_local(
            _valid_row(),
            period="2026-11",
            organization="tenant-a",
        ),
        sms=SmsStatusView(
            employee_id="emp-1",
            suppressed=True,
            suppression_reason="opt_out",
            claim_state=None,
        ),
        ledger_blocks=False,
    )
    assert result.outcome is Outcome.SKIPPED_SUPPRESSED


def test_already_sent_claim_skipped() -> None:
    result = apply_sms_guards(
        classify_local(
            _valid_row(),
            period="2026-11",
            organization="tenant-a",
        ),
        sms=SmsStatusView(
            employee_id="emp-1",
            suppressed=False,
            suppression_reason=None,
            claim_state="sent",
        ),
        ledger_blocks=False,
    )
    assert result.outcome is Outcome.SKIPPED_ALREADY_SENT


def test_ledger_blocks() -> None:
    result = apply_sms_guards(
        classify_local(
            _valid_row(),
            period="2026-11",
            organization="tenant-a",
        ),
        sms=None,
        ledger_blocks=True,
    )
    assert result.outcome is Outcome.SKIPPED_LEDGER
