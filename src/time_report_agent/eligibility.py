"""Eligibility from CSV name/phone/hours, with optional MCP SMS guards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from time_report_agent.csv_queue import LocalDisposition, QueueRow
from time_report_agent.message import build_reminder_sms


class Outcome(str, Enum):
    ELIGIBLE = "eligible"
    SKIPPED_NO_MISSING_HOURS = "skipped_no_missing_hours"
    SKIPPED_ALREADY_SENT = "skipped_already_sent"
    SKIPPED_SUPPRESSED = "skipped_suppressed"
    SKIPPED_LEDGER = "skipped_ledger"
    INVALID = "invalid"
    SENT = "sent"
    REPLAYED = "replayed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class Candidate:
    row: QueueRow
    outcome: Outcome
    reason: str
    missing_hours: float | None = None
    sms_preview: str | None = None


@dataclass(frozen=True, slots=True)
class SmsStatusView:
    employee_id: str
    suppressed: bool
    suppression_reason: str | None
    claim_state: str | None


def classify_local(row: QueueRow, *, period: str, organization: str) -> Candidate:
    if row.disposition is LocalDisposition.INVALID:
        return Candidate(
            row=row,
            outcome=Outcome.INVALID,
            reason=row.reason or "invalid row",
            missing_hours=row.missing_hours,
        )
    if row.disposition is LocalDisposition.SKIPPED_NO_MISSING_HOURS:
        return Candidate(
            row=row,
            outcome=Outcome.SKIPPED_NO_MISSING_HOURS,
            reason=row.reason or "hours is zero",
            missing_hours=row.missing_hours,
        )
    preview = build_reminder_sms(
        employee_name=row.employee_name,
        missing_hours=row.missing_hours,
        period=period,
        organization=organization,
    )
    return Candidate(
        row=row,
        outcome=Outcome.ELIGIBLE,
        reason="csv name/phone/hours checks passed",
        missing_hours=row.missing_hours,
        sms_preview=preview,
    )


def apply_sms_guards(
    candidate: Candidate,
    *,
    sms: SmsStatusView | None,
    ledger_blocks: bool,
) -> Candidate:
    """Apply suppression / same-day claim guards. Does not use Excel reporting."""
    if candidate.outcome is not Outcome.ELIGIBLE:
        return candidate

    if ledger_blocks:
        return Candidate(
            row=candidate.row,
            outcome=Outcome.SKIPPED_LEDGER,
            reason="local ledger already has a same-day claim",
            missing_hours=candidate.missing_hours,
            sms_preview=candidate.sms_preview,
        )

    if sms is not None and sms.suppressed:
        return Candidate(
            row=candidate.row,
            outcome=Outcome.SKIPPED_SUPPRESSED,
            reason=f"sms suppressed ({sms.suppression_reason or 'unknown'})",
            missing_hours=candidate.missing_hours,
            sms_preview=candidate.sms_preview,
        )

    if sms is not None and sms.claim_state in {"pending", "sent", "uncertain"}:
        return Candidate(
            row=candidate.row,
            outcome=Outcome.SKIPPED_ALREADY_SENT,
            reason=f"server claim already {sms.claim_state} today",
            missing_hours=candidate.missing_hours,
            sms_preview=candidate.sms_preview,
        )

    return candidate
