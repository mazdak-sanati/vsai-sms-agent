from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from time_report_agent.config import AppConfig, REQUIRED_MCP_TOOLS
from time_report_agent.eligibility import Outcome
from time_report_agent.ledger import Ledger
from time_report_agent.mcp_client import FakeMcpClient, require_tools
from time_report_agent.pipeline import run_pipeline
from time_report_agent.report import RunSummary
from tests.conftest import write_csv


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("Europe/Stockholm"))


def _csv(tmp_path: Path, *, hours: str = "8") -> Path:
    return write_csv(
        tmp_path / "list.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        f"emp-1;Synthetic Person;0701234567;2026-11-05;{hours}\n",
    )


@pytest.mark.asyncio
async def test_dry_run_uses_csv_and_never_calls_send(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    summary = await run_pipeline(
        base_config,
        fake_mcp,
        csv_path=_csv(tmp_path, hours="8"),
        period="2026-11",
        send=False,
        now=NOW,
        ledger=Ledger(base_config.ledger_path),
    )
    assert fake_mcp.send_calls == []
    assert summary.counts().get("eligible") == 1
    text = summary.format()
    assert "0701234567" not in text
    assert "missing_hours=8.0" in text
    assert "Hej Synthetic!" in text
    assert "source=list.csv" in text


@pytest.mark.asyncio
async def test_send_invokes_mcp_tool(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    summary = await run_pipeline(
        base_config,
        fake_mcp,
        csv_path=_csv(tmp_path),
        period="2026-11",
        send=True,
        now=NOW,
        ledger=Ledger(base_config.ledger_path),
    )
    assert len(fake_mcp.send_calls) == 1
    call = fake_mcp.send_calls[0]
    assert call["employee_id"] == "emp-1"
    assert call["period"] == "2026-11"
    assert call["to"].startswith("+46")
    assert "tidrapport" in call["message"].lower()
    assert "workbook_id" not in call
    assert "template_id" not in call
    assert summary.candidates[0].outcome is Outcome.SENT


@pytest.mark.asyncio
async def test_second_send_same_day_skipped(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    ledger = Ledger(base_config.ledger_path)
    await run_pipeline(
        base_config,
        fake_mcp,
        csv_path=_csv(tmp_path),
        period="2026-11",
        send=True,
        now=NOW,
        ledger=ledger,
    )
    summary = await run_pipeline(
        base_config,
        fake_mcp,
        csv_path=_csv(tmp_path),
        period="2026-11",
        send=True,
        now=NOW,
        ledger=ledger,
    )
    assert len(fake_mcp.send_calls) == 1
    assert summary.candidates[0].outcome is Outcome.SKIPPED_LEDGER


@pytest.mark.asyncio
async def test_suppressed_employee_skipped(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    fake_mcp.sms["emp-1"] = {
        "employee_id": "emp-1",
        "period": "2026-11",
        "claim": None,
        "suppression": {"reason": "opt_out", "active": True},
    }
    summary = await run_pipeline(
        base_config,
        fake_mcp,
        csv_path=_csv(tmp_path),
        period="2026-11",
        send=True,
        now=NOW,
        ledger=Ledger(base_config.ledger_path),
    )
    assert fake_mcp.send_calls == []
    assert summary.candidates[0].outcome is Outcome.SKIPPED_SUPPRESSED


@pytest.mark.asyncio
async def test_missing_tool_fails_closed() -> None:
    with pytest.raises(Exception, match="missing required tools"):
        require_tools({"list_workbooks"})  # send_sms / get_sms_status missing


@pytest.mark.asyncio
async def test_hours_zero_skipped_without_send(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    summary = await run_pipeline(
        base_config,
        fake_mcp,
        csv_path=_csv(tmp_path, hours="0"),
        period="2026-11",
        send=True,
        now=NOW,
        ledger=Ledger(base_config.ledger_path),
    )
    assert fake_mcp.send_calls == []
    assert summary.candidates[0].outcome is Outcome.SKIPPED_NO_MISSING_HOURS


@pytest.mark.asyncio
async def test_replayed_result(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    fake_mcp.send_results["emp-1"] = {
        "ok": True,
        "data": {
            "employee_id": "emp-1",
            "period": "2026-11",
            "state": "sent",
            "provider": "mock",
            "provider_message_id": "mock-1",
            "replayed": True,
        },
    }
    summary = await run_pipeline(
        base_config,
        fake_mcp,
        csv_path=_csv(tmp_path),
        period="2026-11",
        send=True,
        now=NOW,
        ledger=Ledger(base_config.ledger_path),
    )
    assert summary.candidates[0].outcome is Outcome.REPLAYED


@pytest.mark.asyncio
async def test_summary_redacts_full_phone(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    summary = await run_pipeline(
        base_config,
        fake_mcp,
        csv_path=_csv(tmp_path),
        period="2026-11",
        send=False,
        now=NOW,
        ledger=Ledger(base_config.ledger_path),
    )
    text = summary.format()
    assert "0701234567" not in text
    assert "+46701234567" not in text
    assert "emp-1" in text
    assert "phone=***" in text


def test_required_tools_constant() -> None:
    assert REQUIRED_MCP_TOOLS == {
        "get_sms_status",
        "send_sms",
    }


def test_run_summary_exit_flags() -> None:
    summary = RunSummary(
        period="2026-11",
        organization_id="tenant-a",
        workbook_id="wb-1",
        template_id="timesheet_reminder",
        mode="dry-run",
    )
    assert not summary.has_failed_or_uncertain()
