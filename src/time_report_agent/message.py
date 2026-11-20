"""Build reminder SMS text from CSV fields."""

from __future__ import annotations

import re

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def first_name(employee_name: str) -> str:
    parts = employee_name.strip().split()
    return parts[0] if parts else "du"


def sanitize_fragment(text: str, *, max_len: int = 40) -> str:
    cleaned = _CTRL.sub("", text).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned


def format_missing_hours(missing_hours: float | None) -> str:
    if missing_hours is None:
        return "okända"
    if float(missing_hours).is_integer():
        return str(int(missing_hours))
    return f"{missing_hours:.2f}".rstrip("0").rstrip(".")


def build_reminder_sms(
    *,
    employee_name: str,
    missing_hours: float | None,
    period: str,
    organization: str,
) -> str:
    """Courteous Swedish reminder using CSV name/hours and the run period."""
    name = sanitize_fragment(first_name(employee_name), max_len=30)
    org = sanitize_fragment(organization, max_len=40)
    hours = format_missing_hours(missing_hours)
    body = (
        f"Hej {name}! Det saknas {hours} timmar i din tidrapport för {period}. "
        f"Vänligen komplettera tidrapporten. /{org}"
    )
    # Keep within a conservative single-segment-ish budget for GSM/Unicode mix.
    if len(body) > 320:
        body = body[:317] + "..."
    return body
