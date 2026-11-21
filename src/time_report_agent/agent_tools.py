"""Deterministic tools the chat agent may call. Side effects stay gated."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from time_report_agent.config import AppConfig
from time_report_agent.csv_queue import load_queue
from time_report_agent.eligibility import Outcome, apply_sms_guards, classify_local
from time_report_agent.ledger import Ledger
from time_report_agent.mcp_client import (
    McpClientError,
    TimeReportMcp,
    sms_view_from_result,
)
from time_report_agent.phone import redact_phone
from time_report_agent.pipeline import inside_send_window, local_today


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_sms_candidates",
            "description": (
                "Read queue/list.csv and return who needs an SMS reminder plus the "
                "proposed SMS text. Use this when the user asks who to contact or "
                "to send SMS to employees. Phones are redacted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Optional YYYY-MM override; defaults to session period",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_sms_to_candidates",
            "description": (
                "Send SMS reminders through the TimeReport MCP server for eligible "
                "CSV recipients. Always list candidates first. This tool still asks "
                "the human operator to type y/N in the terminal before any send."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Optional YYYY-MM override; defaults to session period",
                    },
                    "user_requested_send": {
                        "type": "boolean",
                        "description": (
                            "Must be true only when the user clearly asked to send SMS now"
                        ),
                    },
                },
                "required": ["user_requested_send"],
                "additionalProperties": False,
            },
        },
    },
]


class AgentToolError(RuntimeError):
    pass


class AgentToolkit:
    def __init__(
        self,
        config: AppConfig,
        client: TimeReportMcp,
        *,
        csv_path: Path,
        period: str,
        ask_confirm: Any | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.csv_path = csv_path
        self.period = period
        self.ledger = Ledger(config.ledger_path)
        self._ask_confirm = ask_confirm or _default_ask_confirm

    def _resolve_period(self, period: str | None) -> str:
        value = (period or self.period or "").strip()
        if not value:
            raise AgentToolError("period is required (YYYY-MM)")
        return value

    async def list_sms_candidates(self, period: str | None = None) -> dict[str, Any]:
        use_period = self._resolve_period(period)
        print(f"[tool] reading CSV {self.csv_path} for period {use_period}", flush=True)
        queue = load_queue(self.csv_path, period=use_period)
        if queue.header_error:
            return {"ok": False, "error": queue.header_error, "candidates": []}

        day = local_today(self.config.timezone)
        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        print(f"[tool] CSV rows={len(queue.rows)}; checking MCP sms status...", flush=True)

        for row in queue.rows:
            local = classify_local(
                row,
                period=use_period,
                organization=self.config.organization_id,
            )
            if local.outcome is not Outcome.ELIGIBLE:
                skipped.append(
                    {
                        "employee_id": row.employee_id or f"row-{row.row_number}",
                        "outcome": local.outcome.value,
                        "reason": local.reason,
                    }
                )
                continue

            try:
                sms_result = await self.client.get_sms_status(
                    employee_id=row.employee_id,
                    period=use_period,
                    local_date=day.isoformat(),
                )
                sms = sms_view_from_result(sms_result, employee_id=row.employee_id)
            except McpClientError as exc:
                skipped.append(
                    {
                        "employee_id": row.employee_id,
                        "outcome": "failed",
                        "reason": str(exc),
                    }
                )
                continue

            guarded = apply_sms_guards(
                local,
                sms=sms,
                ledger_blocks=self.ledger.blocks_send(
                    organization_id=self.config.organization_id,
                    employee_id=row.employee_id,
                    period=use_period,
                    local_date=day,
                ),
            )
            payload = {
                "employee_id": row.employee_id,
                "first_name": (row.employee_name.split() or [""])[0],
                "phone": redact_phone(row.telephone_e164 or row.telephone_raw),
                "missing_hours": row.missing_hours,
                "entry_date": row.entry_date.isoformat() if row.entry_date else None,
                "sms_text": guarded.sms_preview,
                "outcome": guarded.outcome.value,
                "reason": guarded.reason,
            }
            if guarded.outcome is Outcome.ELIGIBLE:
                candidates.append(payload)
            else:
                skipped.append(payload)

        return {
            "ok": True,
            "period": use_period,
            "organization": self.config.organization_id,
            "source": str(self.csv_path),
            "eligible_count": len(candidates),
            "candidates": candidates,
            "skipped": skipped,
            "note": (
                "sms_text is composed by this agent from CSV fields; "
                "MCP send_sms delivers the same to+message when confirmed."
            ),
        }

    async def send_sms_to_candidates(
        self,
        *,
        user_requested_send: bool,
        period: str | None = None,
    ) -> dict[str, Any]:
        if not user_requested_send:
            return {
                "ok": False,
                "error": (
                    "user_requested_send must be true. First call list_sms_candidates "
                    "and wait for the user to confirm they want to send."
                ),
            }

        listed = await self.list_sms_candidates(period)
        if not listed.get("ok"):
            return listed
        eligible = listed.get("candidates") or []
        if not eligible:
            return {
                "ok": True,
                "sent": [],
                "message": "No eligible recipients to send.",
                "listed": listed,
            }

        print("")
        print(f"About to send via MCP to {len(eligible)} recipient(s):")
        for item in eligible:
            print(
                f"  - employee_id={item['employee_id']} "
                f"first_name={item.get('first_name')} phone={item.get('phone')}"
            )
            if item.get("sms_text"):
                print(f"    preview: {item['sms_text']}")
        print("MCP will send the preview text above to those CSV phone numbers.")

        if not self._ask_confirm(len(eligible)):
            return {
                "ok": True,
                "cancelled": True,
                "message": "Operator cancelled send in the terminal.",
                "eligible_count": len(eligible),
            }

        use_period = listed["period"]
        now = datetime.now(ZoneInfo(self.config.timezone))
        if not inside_send_window(self.config, now=now):
            return {
                "ok": False,
                "error": "outside configured send window; no SMS sent",
                "eligible_count": len(eligible),
            }

        # Re-read CSV for real E.164 + message (LLM payload only has redacted phones).
        queue = load_queue(self.csv_path, period=use_period)
        if queue.header_error:
            return {"ok": False, "error": queue.header_error}
        by_id = {
            row.employee_id: row
            for row in queue.rows
            if row.employee_id
        }

        day = local_today(self.config.timezone, now=now)
        results: list[dict[str, Any]] = []

        for item in eligible:
            employee_id = str(item["employee_id"])
            row = by_id.get(employee_id)
            if row is None or not row.telephone_e164:
                results.append(
                    {
                        "employee_id": employee_id,
                        "outcome": "failed",
                        "reason": "employee missing from CSV or phone invalid",
                    }
                )
                continue
            local = classify_local(
                row,
                period=use_period,
                organization=self.config.organization_id,
            )
            body = local.sms_preview or ""
            if not body:
                results.append(
                    {
                        "employee_id": employee_id,
                        "outcome": "failed",
                        "reason": "could not compose SMS body",
                    }
                )
                continue
            try:
                with self.ledger.exclusive_lock():
                    inserted = self.ledger.try_insert_pending(
                        organization_id=self.config.organization_id,
                        employee_id=employee_id,
                        period=use_period,
                        local_date=day,
                    )
                    if not inserted:
                        results.append(
                            {
                                "employee_id": employee_id,
                                "outcome": "skipped_ledger",
                                "reason": "local ledger already has a same-day claim",
                            }
                        )
                        continue

                    print(
                        f"[mcp-send] employee_id={employee_id} period={use_period} "
                        f"to={redact_phone(row.telephone_e164)} "
                        f"body_chars={len(body)}",
                        flush=True,
                    )
                    result = await self.client.send_sms(
                        to=row.telephone_e164,
                        message=body,
                        employee_id=employee_id,
                        period=use_period,
                    )
                    print(
                        f"[mcp-send] response ok={result.ok} "
                        f"code={result.error_code} data={result.data}",
                        flush=True,
                    )
                    status_check = await self.client.get_sms_status(
                        employee_id=employee_id,
                        period=use_period,
                        local_date=day.isoformat(),
                    )
                    print(
                        f"[mcp-send] get_sms_status ok={status_check.ok} "
                        f"data={status_check.data}",
                        flush=True,
                    )
                    if not result.ok:
                        status = (
                            "uncertain"
                            if result.error_code in {"uncertain", "provider_timeout"}
                            else "failed"
                        )
                        self.ledger.finalize(
                            organization_id=self.config.organization_id,
                            employee_id=employee_id,
                            period=use_period,
                            local_date=day,
                            status=status,
                        )
                        results.append(
                            {
                                "employee_id": employee_id,
                                "outcome": status,
                                "reason": f"{result.error_code}: {result.error_message}",
                            }
                        )
                        continue

                    data = result.data or {}
                    replayed = bool(data.get("replayed"))
                    state = str(data.get("state") or ("replayed" if replayed else "sent"))
                    if state == "uncertain":
                        final_status = "uncertain"
                    elif replayed or state == "sent":
                        final_status = "replayed" if replayed else "sent"
                    else:
                        final_status = "failed" if state == "rejected" else state
                    self.ledger.finalize(
                        organization_id=self.config.organization_id,
                        employee_id=employee_id,
                        period=use_period,
                        local_date=day,
                        status=final_status,
                        provider_message_id=(
                            str(data["provider_message_id"])
                            if data.get("provider_message_id") is not None
                            else None
                        ),
                    )
                    results.append(
                        {
                            "employee_id": employee_id,
                            "outcome": final_status,
                            "provider_message_id": data.get("provider_message_id"),
                            "provider_detail": data.get("provider_detail"),
                            "destination_last4": data.get("destination_last4"),
                            "sms_status": status_check.data,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "employee_id": employee_id,
                        "outcome": "failed",
                        "reason": str(exc),
                    }
                )

        return {
            "ok": True,
            "period": use_period,
            "results": results,
            "eligible_count": len(eligible),
        }

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "list_sms_candidates":
                result = await self.list_sms_candidates(arguments.get("period"))
            elif name == "send_sms_to_candidates":
                result = await self.send_sms_to_candidates(
                    user_requested_send=bool(arguments.get("user_requested_send")),
                    period=arguments.get("period"),
                )
            else:
                result = {"ok": False, "error": f"Unknown tool: {name}"}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)}
        return json.dumps(result, ensure_ascii=False)


def _default_ask_confirm(eligible_count: int) -> bool:
    if eligible_count <= 0:
        return False
    if not sys.stdin.isatty():
        print("Cannot confirm send: stdin is not interactive.", file=sys.stderr)
        return False
    try:
        answer = input(
            f"Send SMS via MCP to {eligible_count} eligible recipient(s)? [y/N]: "
        )
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}
