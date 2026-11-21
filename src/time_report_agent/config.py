"""Load and validate CLI configuration from environment and .env."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

REQUIRED_MCP_TOOLS = frozenset(
    {
        "get_sms_status",
        "send_sms",
    }
)

# Environment keys allowed through to the MCP child process.
_MCP_CHILD_ENV_KEYS = frozenset(
    {
        "TIMEREPORT_CONFIG",
        "TIMEREPORT_STDIO_TENANT",
        "TIMEREPORT_DATA_DIR",
        "DATABASE_URL",
        "SMS_PROVIDER",
        "SMS_LIVE_SEND",
        "ELKS_API_USERNAME",
        "ELKS_API_PASSWORD",
        "ELKS_FROM",
        "TIMEREPORT_SMS_TRACE",
        "TIMEREPORT_HTTP_HOST",
        "TIMEREPORT_HTTP_PORT",
        "TIMEREPORT_RESOURCE_SERVER_URL",
        "TIMEREPORT_AUTH_ISSUER_URL",
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "LANG",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }
)


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    organization_id: str
    stdio_tenant: str
    mcp_command: list[str]
    mcp_cwd: Path | None
    timereport_config: Path
    workbook_id: str | None
    sms_template_id: str
    default_csv_path: Path
    ledger_path: Path
    timezone: str
    send_window_start: time
    send_window_end: time
    send_weekdays_only: bool
    child_env: dict[str, str]
    ollama_host: str
    ollama_model: str
    ollama_think: bool
    default_period: str | None


def _parse_time(value: str, *, field: str) -> time:
    text = value.strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ConfigError(f"{field} must be HH:MM, got {value!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        return time(hour=hour, minute=minute)
    except ValueError as exc:
        raise ConfigError(f"{field} must be HH:MM, got {value!r}") from exc


def _parse_command(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        raise ConfigError("MCP_COMMAND is empty")
    return shlex.split(text, posix=False)


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_child_env(environ: dict[str, str]) -> dict[str, str]:
    """Allowlisted environment for the MCP child. Never forwards LLM_* keys."""
    out: dict[str, str] = {}
    for key, value in environ.items():
        upper = key.upper()
        if upper.startswith("LLM_"):
            continue
        if upper in _MCP_CHILD_ENV_KEYS or key in _MCP_CHILD_ENV_KEYS:
            out[key] = value
    # Ensure the critical keys are present when set under exact names.
    for key in (
        "TIMEREPORT_CONFIG",
        "TIMEREPORT_STDIO_TENANT",
        "TIMEREPORT_DATA_DIR",
        "DATABASE_URL",
        "SMS_PROVIDER",
        "SMS_LIVE_SEND",
        "ELKS_API_USERNAME",
        "ELKS_API_PASSWORD",
        "ELKS_FROM",
        "TIMEREPORT_SMS_TRACE",
    ):
        if key in environ and environ[key] != "":
            out[key] = environ[key]
    return out


def load_config(
    *,
    env_file: Path | None = None,
    environ: dict[str, str] | None = None,
) -> AppConfig:
    if environ is None:
        if env_file is not None:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)
        env = dict(os.environ)
    else:
        env = dict(environ)

    organization_id = (env.get("ORGANIZATION_ID") or "").strip()
    stdio_tenant = (env.get("TIMEREPORT_STDIO_TENANT") or "").strip()
    if not organization_id:
        raise ConfigError("ORGANIZATION_ID is required")
    if not stdio_tenant:
        raise ConfigError("TIMEREPORT_STDIO_TENANT is required")
    if organization_id != stdio_tenant:
        raise ConfigError(
            "ORGANIZATION_ID must equal TIMEREPORT_STDIO_TENANT "
            f"(got organization={organization_id!r}, stdio={stdio_tenant!r})"
        )

    config_path_raw = (env.get("TIMEREPORT_CONFIG") or "").strip()
    if not config_path_raw:
        raise ConfigError("TIMEREPORT_CONFIG is required")
    timereport_config = Path(config_path_raw)

    mcp_command = _parse_command(
        env.get("MCP_COMMAND", "timereport-mcp --transport stdio")
    )
    mcp_cwd_raw = (env.get("MCP_CWD") or "").strip()
    mcp_cwd = Path(mcp_cwd_raw) if mcp_cwd_raw else None

    workbook_raw = (env.get("WORKBOOK_ID") or "").strip()
    workbook_id = workbook_raw or None

    sms_template_id = (env.get("SMS_TEMPLATE_ID") or "timesheet_reminder").strip()
    if not sms_template_id:
        raise ConfigError("SMS_TEMPLATE_ID is empty")

    default_csv = Path(
        (env.get("DEFAULT_CSV_PATH") or "").strip()
        or r"C:\Users\Mazda\Documents\time_report_agent\queue\list.csv"
    )
    ledger_path = Path(
        (env.get("LEDGER_PATH") or "").strip()
        or r"C:\Users\Mazda\Documents\time_report_agent\data\send_ledger.db"
    )
    timezone = (env.get("TIMEZONE") or "Europe/Stockholm").strip()
    send_window_start = _parse_time(
        env.get("SEND_WINDOW_START", "08:00"), field="SEND_WINDOW_START"
    )
    send_window_end = _parse_time(
        env.get("SEND_WINDOW_END", "18:00"), field="SEND_WINDOW_END"
    )
    if send_window_end <= send_window_start:
        raise ConfigError("SEND_WINDOW_END must be after SEND_WINDOW_START")

    child_env = build_child_env(env)
    child_env["TIMEREPORT_STDIO_TENANT"] = stdio_tenant
    child_env["TIMEREPORT_CONFIG"] = str(timereport_config)

    ollama_host = (env.get("OLLAMA_HOST") or "http://127.0.0.1:11434").strip()
    ollama_model = (env.get("OLLAMA_MODEL") or "gemma4:e4b").strip()
    # gemma4 thinking mode can look like a hang; default off for terminal UX.
    ollama_think = _truthy(env.get("OLLAMA_THINK"), default=False)
    default_period_raw = (env.get("DEFAULT_PERIOD") or "").strip()
    default_period = default_period_raw or None

    return AppConfig(
        organization_id=organization_id,
        stdio_tenant=stdio_tenant,
        mcp_command=mcp_command,
        mcp_cwd=mcp_cwd,
        timereport_config=timereport_config,
        workbook_id=workbook_id,
        sms_template_id=sms_template_id,
        default_csv_path=default_csv,
        ledger_path=ledger_path,
        timezone=timezone,
        send_window_start=send_window_start,
        send_window_end=send_window_end,
        send_weekdays_only=_truthy(env.get("SEND_WEEKDAYS_ONLY"), default=True),
        child_env=child_env,
        ollama_host=ollama_host,
        ollama_model=ollama_model,
        ollama_think=ollama_think,
        default_period=default_period,
    )
