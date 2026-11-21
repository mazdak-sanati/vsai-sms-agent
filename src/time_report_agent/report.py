"""Redacted run summaries for stdout."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from time_report_agent.eligibility import Candidate, Outcome
from time_report_agent.message import first_name
from time_report_agent.phone import redact_phone


@dataclass
class RunSummary:
    period: str
    organization_id: str
    workbook_id: str | None
    template_id: str
    mode: str
    source: str = "list.csv"
    candidates: list[Candidate] = field(default_factory=list)
    send_window_ok: bool = True
    config_error: str | None = None
    phone_redactions: dict[str, str] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for item in self.candidates:
            counter[item.outcome.value] += 1
        return dict(sorted(counter.items()))

    def has_failed_or_uncertain(self) -> bool:
        return any(
            c.outcome in {Outcome.FAILED, Outcome.UNCERTAIN} for c in self.candidates
        )

    def format(self) -> str:
        lines: list[str] = []
        lines.append(f"organization={self.organization_id}")
        lines.append(f"period={self.period}")
        lines.append(f"source={self.source}")
        lines.append(f"mode={self.mode}")
        lines.append(
            "note=agent chooses recipients and message text from CSV; "
            "MCP send_sms only delivers caller-provided to+message"
        )
        if not self.send_window_ok:
            lines.append("send_window=outside (no sends)")
        if self.config_error:
            lines.append(f"error={self.config_error}")
        counts = self.counts()
        if counts:
            lines.append("counts=" + ", ".join(f"{k}:{v}" for k, v in counts.items()))
        else:
            lines.append("counts=(none)")
        for item in self.candidates:
            emp = item.row.employee_id or f"row-{item.row.row_number}"
            phone = self.phone_redactions.get(emp) or redact_phone(
                item.row.telephone_e164 or item.row.telephone_raw
            )
            if item.missing_hours is None and item.row.hours_raw.strip() == "":
                missing = " missing_hours=<blank/unreported>"
            elif item.missing_hours is not None:
                missing = f" missing_hours={item.missing_hours}"
            else:
                missing = ""
            # Show first name only (needed to verify CSV-driven SMS preview).
            fname = first_name(item.row.employee_name) if item.row.employee_name else ""
            name_bit = f" first_name={fname}" if fname else ""
            lines.append(
                f"  employee_id={emp} outcome={item.outcome.value}"
                f"{name_bit} phone={phone}{missing} reason={item.reason}"
            )
            if item.sms_preview and item.outcome in {
                Outcome.ELIGIBLE,
                Outcome.SENT,
                Outcome.REPLAYED,
            }:
                lines.append(f"    sms_preview={item.sms_preview}")
        return "\n".join(lines)
