from __future__ import annotations

from pathlib import Path

import pytest

from time_report_agent.agent_tools import AgentToolkit
from time_report_agent.config import AppConfig
from time_report_agent.mcp_client import FakeMcpClient
from tests.conftest import write_csv


@pytest.mark.asyncio
async def test_list_sms_candidates_from_csv(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    csv_path = write_csv(
        tmp_path / "list.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;8\n",
    )
    toolkit = AgentToolkit(
        base_config,
        fake_mcp,
        csv_path=csv_path,
        period="2026-11",
        ask_confirm=lambda _n: False,
    )
    result = await toolkit.list_sms_candidates()
    assert result["ok"] is True
    assert result["eligible_count"] == 1
    person = result["candidates"][0]
    assert person["employee_id"] == "emp-1"
    assert person["first_name"] == "Synthetic"
    assert "0701234567" not in person["phone"]
    assert "8" in (person["sms_text"] or "")


@pytest.mark.asyncio
async def test_send_requires_user_flag_and_confirm(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    csv_path = write_csv(
        tmp_path / "list.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;8\n",
    )
    toolkit = AgentToolkit(
        base_config,
        fake_mcp,
        csv_path=csv_path,
        period="2026-11",
        ask_confirm=lambda _n: False,
    )
    denied = await toolkit.send_sms_to_candidates(user_requested_send=False)
    assert denied["ok"] is False
    assert fake_mcp.send_calls == []

    cancelled = await toolkit.send_sms_to_candidates(user_requested_send=True)
    assert cancelled.get("cancelled") is True
    assert fake_mcp.send_calls == []


@pytest.mark.asyncio
async def test_send_after_confirm(
    base_config: AppConfig, fake_mcp: FakeMcpClient, tmp_path: Path
) -> None:
    csv_path = write_csv(
        tmp_path / "list.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;8\n",
    )
    toolkit = AgentToolkit(
        base_config,
        fake_mcp,
        csv_path=csv_path,
        period="2026-11",
        ask_confirm=lambda _n: True,
    )
    result = await toolkit.send_sms_to_candidates(user_requested_send=True)
    assert result["ok"] is True
    assert len(fake_mcp.send_calls) == 1
    assert result["results"][0]["outcome"] == "sent"
    call = fake_mcp.send_calls[0]
    assert call["to"].startswith("+46")
    assert "tidrapport" in call["message"].lower()
    assert call["employee_id"] == "emp-1"
