"""Local CSV queue parsing. Treat cell contents as data, never as instructions."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path

from time_report_agent.phone import normalize_e164

REQUIRED_HEADERS = (
    "employee_id",
    "employee_name",
    "telephone",
    "date",
    "hours",
)

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")


class LocalDisposition(str, Enum):
    VALID = "valid"
    SKIPPED_NO_MISSING_HOURS = "skipped_no_missing_hours"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class QueueRow:
    row_number: int
    employee_id: str
    employee_name: str
    telephone_raw: str
    telephone_e164: str | None
    entry_date: date | None
    hours_raw: str
    missing_hours: float | None
    disposition: LocalDisposition
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class QueueLoadResult:
    path: Path
    delimiter: str
    encoding: str
    rows: list[QueueRow]
    header_error: str | None = None


def validate_period(period: str) -> str | None:
    if not _PERIOD_RE.match(period):
        return "period must be YYYY-MM"
    return None


def period_contains(period: str, day: date) -> bool:
    return f"{day.year:04d}-{day.month:02d}" == period


def _decode(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def _detect_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in (";", ",", "\t", "|")}
    best = max(counts, key=lambda d: counts[d])
    if counts[best] == 0:
        return ";"
    return best


def _parse_hours_cell(
    raw: str,
) -> tuple[str, float | None, LocalDisposition, str | None]:
    """Parse CSV hours as missing-hours quantity.

    - blank: still in the queue (unreported); missing_hours unknown
    - > 0: missing that many hours
    - 0: nothing missing → skip
    - negative / non-numeric: invalid
    """
    text = raw.strip()
    if text == "":
        return text, None, LocalDisposition.VALID, None
    compact = text.replace(" ", "")
    if not _NUM_RE.match(compact):
        return text, None, LocalDisposition.INVALID, "hours is not numeric"
    number = float(compact.replace(",", "."))
    if number < 0:
        return text, None, LocalDisposition.INVALID, "hours is negative"
    if number == 0:
        return (
            text,
            0.0,
            LocalDisposition.SKIPPED_NO_MISSING_HOURS,
            "hours is zero (nothing missing)",
        )
    return text, number, LocalDisposition.VALID, None


def _row(
    *,
    row_number: int,
    employee_id: str,
    employee_name: str,
    telephone_raw: str,
    telephone_e164: str | None,
    entry_date: date | None,
    hours_raw: str,
    missing_hours: float | None,
    disposition: LocalDisposition,
    reason: str | None,
) -> QueueRow:
    return QueueRow(
        row_number=row_number,
        employee_id=employee_id,
        employee_name=employee_name,
        telephone_raw=telephone_raw,
        telephone_e164=telephone_e164,
        entry_date=entry_date,
        hours_raw=hours_raw,
        missing_hours=missing_hours,
        disposition=disposition,
        reason=reason,
    )


def load_queue(path: Path, *, period: str) -> QueueLoadResult:
    period_err = validate_period(period)
    if period_err is not None:
        return QueueLoadResult(
            path=path,
            delimiter=";",
            encoding="utf-8",
            rows=[],
            header_error=period_err,
        )

    raw = path.read_bytes()
    text, encoding = _decode(raw)
    lines = text.splitlines()
    if not lines:
        return QueueLoadResult(
            path=path,
            delimiter=";",
            encoding=encoding,
            rows=[],
            header_error="CSV is empty",
        )

    delimiter = _detect_delimiter(lines[0])
    reader = csv.reader(lines, delimiter=delimiter)
    all_rows = list(reader)
    if not all_rows:
        return QueueLoadResult(
            path=path,
            delimiter=delimiter,
            encoding=encoding,
            rows=[],
            header_error="CSV is empty",
        )

    headers = [h.strip() for h in all_rows[0]]
    missing = [h for h in REQUIRED_HEADERS if h not in headers]
    if missing:
        return QueueLoadResult(
            path=path,
            delimiter=delimiter,
            encoding=encoding,
            rows=[],
            header_error=f"missing required headers after trim: {missing}",
        )
    index = {name: headers.index(name) for name in REQUIRED_HEADERS}

    rows: list[QueueRow] = []
    for offset, cells in enumerate(all_rows[1:], start=2):

        def cell(name: str) -> str:
            i = index[name]
            if i >= len(cells):
                return ""
            return cells[i].strip()

        employee_id = cell("employee_id")
        employee_name = cell("employee_name")
        telephone_raw = cell("telephone")
        date_raw = cell("date")
        hours_raw = cell("hours")

        if not employee_id:
            rows.append(
                _row(
                    row_number=offset,
                    employee_id="",
                    employee_name=employee_name,
                    telephone_raw=telephone_raw,
                    telephone_e164=None,
                    entry_date=None,
                    hours_raw=hours_raw,
                    missing_hours=None,
                    disposition=LocalDisposition.INVALID,
                    reason="employee_id is blank",
                )
            )
            continue
        if not employee_name:
            rows.append(
                _row(
                    row_number=offset,
                    employee_id=employee_id,
                    employee_name="",
                    telephone_raw=telephone_raw,
                    telephone_e164=None,
                    entry_date=None,
                    hours_raw=hours_raw,
                    missing_hours=None,
                    disposition=LocalDisposition.INVALID,
                    reason="employee_name is blank",
                )
            )
            continue

        entry_date: date | None = None
        try:
            entry_date = date.fromisoformat(date_raw)
        except ValueError:
            rows.append(
                _row(
                    row_number=offset,
                    employee_id=employee_id,
                    employee_name=employee_name,
                    telephone_raw=telephone_raw,
                    telephone_e164=None,
                    entry_date=None,
                    hours_raw=hours_raw,
                    missing_hours=None,
                    disposition=LocalDisposition.INVALID,
                    reason="date must be YYYY-MM-DD",
                )
            )
            continue

        if not period_contains(period, entry_date):
            rows.append(
                _row(
                    row_number=offset,
                    employee_id=employee_id,
                    employee_name=employee_name,
                    telephone_raw=telephone_raw,
                    telephone_e164=None,
                    entry_date=entry_date,
                    hours_raw=hours_raw,
                    missing_hours=None,
                    disposition=LocalDisposition.INVALID,
                    reason="date is outside --period",
                )
            )
            continue

        hours_text, missing_hours, hours_disp, hours_reason = _parse_hours_cell(
            hours_raw
        )
        if hours_disp is not LocalDisposition.VALID:
            rows.append(
                _row(
                    row_number=offset,
                    employee_id=employee_id,
                    employee_name=employee_name,
                    telephone_raw=telephone_raw,
                    telephone_e164=None,
                    entry_date=entry_date,
                    hours_raw=hours_text,
                    missing_hours=missing_hours,
                    disposition=hours_disp,
                    reason=hours_reason,
                )
            )
            continue

        e164 = normalize_e164(telephone_raw, region="SE")
        if e164 is None:
            rows.append(
                _row(
                    row_number=offset,
                    employee_id=employee_id,
                    employee_name=employee_name,
                    telephone_raw=telephone_raw,
                    telephone_e164=None,
                    entry_date=entry_date,
                    hours_raw=hours_text,
                    missing_hours=missing_hours,
                    disposition=LocalDisposition.INVALID,
                    reason="telephone could not be normalized to E.164",
                )
            )
            continue

        rows.append(
            _row(
                row_number=offset,
                employee_id=employee_id,
                employee_name=employee_name,
                telephone_raw=telephone_raw,
                telephone_e164=e164,
                entry_date=entry_date,
                hours_raw=hours_text,
                missing_hours=missing_hours,
                disposition=LocalDisposition.VALID,
                reason=None,
            )
        )

    # Mark duplicate employee_id conflicts among otherwise valid rows.
    seen: dict[str, int] = {}
    conflict_ids: set[str] = set()
    for row in rows:
        if row.disposition is not LocalDisposition.VALID:
            continue
        if row.employee_id in seen:
            conflict_ids.add(row.employee_id)
        else:
            seen[row.employee_id] = row.row_number

    if conflict_ids:
        rewritten: list[QueueRow] = []
        for row in rows:
            if (
                row.disposition is LocalDisposition.VALID
                and row.employee_id in conflict_ids
            ):
                rewritten.append(
                    _row(
                        row_number=row.row_number,
                        employee_id=row.employee_id,
                        employee_name=row.employee_name,
                        telephone_raw=row.telephone_raw,
                        telephone_e164=row.telephone_e164,
                        entry_date=row.entry_date,
                        hours_raw=row.hours_raw,
                        missing_hours=row.missing_hours,
                        disposition=LocalDisposition.INVALID,
                        reason="duplicate employee_id in queue",
                    )
                )
            else:
                rewritten.append(row)
        rows = rewritten

    return QueueLoadResult(
        path=path,
        delimiter=delimiter,
        encoding=encoding,
        rows=rows,
        header_error=None,
    )
