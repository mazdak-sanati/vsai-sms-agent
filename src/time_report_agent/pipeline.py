"""End-to-end run pipeline for dry-run and send modes."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from time_report_agent.config import AppConfig
from time_report_agent.csv_queue import load_queue
from time_report_agent.eligibility import (
    Candidate,
    Outcome,
    apply_sms_guards,
    classify_local,
)
from time_report_agent.ledger import Ledger
from time_report_agent.mcp_client import (
    McpClientError,
    TimeReportMcp,
    resolve_workbook_id,
    sms_view_from_result,
)
from time_report_agent.phone import redact_phone
from time_report_agent.report import RunSummary


def local_today(timezone: str, *, now: datetime | None = None) -> date:
    clock = now or datetime.now(ZoneInfo(timezone))
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=ZoneInfo(timezone))
    return clock.astimezone(ZoneInfo(timezone)).date()


def inside_send_window(
    config: AppConfig,
    *,
    now: datetime | None = None,
) -> bool:
    clock = now or datetime.now(ZoneInfo(config.timezone))
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=ZoneInfo(config.timezone))
    local = clock.astimezone(ZoneInfo(config.timezone))
    if config.send_weekdays_only and local.weekday() >= 5:
        return False
    current = local.timetz().replace(tzinfo=None)
    return config.send_window_start <= current < config.send_window_end


async def run_pipeline(
    config: AppConfig,
    client: TimeReportMcp,
    *,
    csv_path: Path,
    period: str,
    send: bool,
    now: datetime | None = None,
    ledger: Ledger | None = None,
) -> RunSummary:
    mode = "send" if send else "dry-run"
    summary = RunSummary(
        period=period,
        organization_id=config.organization_id,
        workbook_id=config.workbook_id,
        template_id=config.sms_template_id,
        mode=mode,
        source="list.csv",
    )

    window_ok = inside_send_window(config, now=now)
    summary.send_window_ok = window_ok
    if send and not window_ok:
        summary.config_error = "outside configured send window"
        send = False
        summary.mode = "send-blocked-window"

    queue = load_queue(csv_path, period=period)
    if queue.header_error:
        summary.config_error = queue.header_error
        return summary

    try:
        listed = await client.list_workbooks()
        workbook_id = resolve_workbook_id(listed, config.workbook_id)
    except McpClientError as exc:
        summary.config_error = str(exc)
        return summary
    summary.workbook_id = workbook_id

    day = local_today(config.timezone, now=now)
    store = ledger or Ledger(config.ledger_path)

    for row in queue.rows:
        local_candidate = classify_local(
            row,
            period=period,
            organization=config.organization_id,
        )
        if local_candidate.outcome is not Outcome.ELIGIBLE:
            summary.candidates.append(local_candidate)
            continue

        try:
            sms_result = await client.get_sms_status(
                employee_id=row.employee_id,
                period=period,
                local_date=day.isoformat(),
            )
            sms = sms_view_from_result(sms_result, employee_id=row.employee_id)
        except McpClientError as exc:
            summary.candidates.append(
                Candidate(
                    row=row,
                    outcome=Outcome.FAILED,
                    reason=str(exc),
                    missing_hours=row.missing_hours,
                    sms_preview=local_candidate.sms_preview,
                )
            )
            continue

        ledger_blocks = store.blocks_send(
            organization_id=config.organization_id,
            employee_id=row.employee_id,
            period=period,
            local_date=day,
        )
        candidate = apply_sms_guards(
            local_candidate,
            sms=sms,
            ledger_blocks=ledger_blocks,
        )
        if candidate.outcome is not Outcome.ELIGIBLE or summary.mode != "send":
            summary.candidates.append(candidate)
            continue

        # Send path — MCP still delivers SMS; body remains the server template.
        try:
            with store.exclusive_lock():
                inserted = store.try_insert_pending(
                    organization_id=config.organization_id,
                    employee_id=row.employee_id,
                    period=period,
                    local_date=day,
                )
                if not inserted:
                    summary.candidates.append(
                        Candidate(
                            row=row,
                            outcome=Outcome.SKIPPED_LEDGER,
                            reason="local ledger already has a same-day claim",
                            missing_hours=candidate.missing_hours,
                            sms_preview=candidate.sms_preview,
                        )
                    )
                    continue

                result = await client.send_sms(
                    workbook_id=workbook_id,
                    employee_id=row.employee_id,
                    period=period,
                    template_id=config.sms_template_id,
                )
                if not result.ok:
                    status = "failed"
                    if result.error_code in {"uncertain", "provider_timeout"}:
                        status = "uncertain"
                    store.finalize(
                        organization_id=config.organization_id,
                        employee_id=row.employee_id,
                        period=period,
                        local_date=day,
                        status=status,
                    )
                    summary.candidates.append(
                        Candidate(
                            row=row,
                            outcome=Outcome.UNCERTAIN
                            if status == "uncertain"
                            else Outcome.FAILED,
                            reason=f"{result.error_code}: {result.error_message}",
                            missing_hours=candidate.missing_hours,
                            sms_preview=candidate.sms_preview,
                        )
                    )
                    continue

                data = result.data or {}
                replayed = bool(data.get("replayed"))
                state = str(data.get("state") or ("replayed" if replayed else "sent"))
                if state == "uncertain":
                    final_status = "uncertain"
                    outcome = Outcome.UNCERTAIN
                elif replayed or state == "sent":
                    final_status = "replayed" if replayed else "sent"
                    outcome = Outcome.REPLAYED if replayed else Outcome.SENT
                else:
                    final_status = state
                    outcome = (
                        Outcome.FAILED
                        if state not in {"sent", "replayed"}
                        else Outcome.SENT
                    )

                store.finalize(
                    organization_id=config.organization_id,
                    employee_id=row.employee_id,
                    period=period,
                    local_date=day,
                    status=final_status,
                    provider_message_id=(
                        str(data["provider_message_id"])
                        if data.get("provider_message_id") is not None
                        else None
                    ),
                )
                summary.candidates.append(
                    Candidate(
                        row=row,
                        outcome=outcome,
                        reason="send_sms accepted"
                        if outcome is Outcome.SENT
                        else f"send_sms {final_status}",
                        missing_hours=candidate.missing_hours,
                        sms_preview=candidate.sms_preview,
                    )
                )
        except TimeoutError as exc:
            store.finalize(
                organization_id=config.organization_id,
                employee_id=row.employee_id,
                period=period,
                local_date=day,
                status="uncertain",
            )
            summary.candidates.append(
                Candidate(
                    row=row,
                    outcome=Outcome.UNCERTAIN,
                    reason=str(exc),
                    missing_hours=candidate.missing_hours,
                    sms_preview=candidate.sms_preview,
                )
            )
        except Exception as exc:  # noqa: BLE001
            existing = store.get(
                organization_id=config.organization_id,
                employee_id=row.employee_id,
                period=period,
                local_date=day,
            )
            if existing is not None and existing.status == "pending":
                store.finalize(
                    organization_id=config.organization_id,
                    employee_id=row.employee_id,
                    period=period,
                    local_date=day,
                    status="uncertain",
                )
                outcome = Outcome.UNCERTAIN
            else:
                outcome = Outcome.FAILED
            summary.candidates.append(
                Candidate(
                    row=row,
                    outcome=outcome,
                    reason=str(exc),
                    missing_hours=candidate.missing_hours,
                    sms_preview=candidate.sms_preview,
                )
            )

    # Attach redacted phone tokens for the formatter without exposing full numbers
    # in Candidate itself beyond what's already on the row.
    summary.phone_redactions = {
        c.row.employee_id: redact_phone(c.row.telephone_e164 or c.row.telephone_raw)
        for c in summary.candidates
        if c.row.employee_id
    }
    return summary
