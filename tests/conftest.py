from __future__ import annotations

from pathlib import Path

import pytest

from time_report_agent.config import AppConfig
from time_report_agent.mcp_client import FakeMcpClient


@pytest.fixture
def tmp_ledger(tmp_path: Path):
    return tmp_path / "ledger.db"


@pytest.fixture
def base_config(tmp_path: Path, tmp_ledger: Path) -> AppConfig:
    return AppConfig(
        organization_id="tenant-a",
        stdio_tenant="tenant-a",
        mcp_command=["timereport-mcp", "--transport", "stdio"],
        mcp_cwd=None,
        timereport_config=tmp_path / "tenants.toml",
        workbook_id=None,
        sms_template_id="timesheet_reminder",
        default_csv_path=tmp_path / "list.csv",
        ledger_path=tmp_ledger,
        timezone="Europe/Stockholm",
        send_window_start=__import__("datetime").time(0, 0),
        send_window_end=__import__("datetime").time(23, 59),
        send_weekdays_only=False,
        child_env={
            "TIMEREPORT_STDIO_TENANT": "tenant-a",
            "TIMEREPORT_CONFIG": str(tmp_path / "tenants.toml"),
            "SMS_PROVIDER": "mock",
            "SMS_LIVE_SEND": "false",
        },
        ollama_host="http://127.0.0.1:11434",
        ollama_model="gemma4:e4b",
        ollama_think=False,
        default_period="2026-11",
    )


@pytest.fixture
def fake_mcp() -> FakeMcpClient:
    return FakeMcpClient()


def write_csv(path: Path, text: str, *, encoding: str = "utf-8-sig") -> Path:
    path.write_text(text, encoding=encoding, newline="")
    return path
