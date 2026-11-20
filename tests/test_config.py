from __future__ import annotations

import pytest

from time_report_agent.config import ConfigError, build_child_env, load_config


def test_organization_must_match_stdio_tenant() -> None:
    with pytest.raises(ConfigError, match="must equal"):
        load_config(
            environ={
                "ORGANIZATION_ID": "tenant-a",
                "TIMEREPORT_STDIO_TENANT": "tenant-b",
                "TIMEREPORT_CONFIG": "C:/tmp/tenants.toml",
            }
        )


def test_llm_keys_not_forwarded_to_child() -> None:
    env = build_child_env(
        {
            "TIMEREPORT_CONFIG": "x",
            "TIMEREPORT_STDIO_TENANT": "tenant-a",
            "SMS_PROVIDER": "mock",
            "LLM_API_KEY": "secret",
            "PATH": "C:\\Windows\\System32",
        }
    )
    assert "LLM_API_KEY" not in env
    assert env["SMS_PROVIDER"] == "mock"
    assert "PATH" in env


def test_load_happy_path() -> None:
    cfg = load_config(
        environ={
            "ORGANIZATION_ID": "tenant-a",
            "TIMEREPORT_STDIO_TENANT": "tenant-a",
            "TIMEREPORT_CONFIG": "C:/tmp/tenants.toml",
            "SMS_TEMPLATE_ID": "timesheet_reminder",
            "DEFAULT_CSV_PATH": "C:/tmp/list.csv",
            "LEDGER_PATH": "C:/tmp/ledger.db",
            "SMS_PROVIDER": "mock",
            "SMS_LIVE_SEND": "false",
        }
    )
    assert cfg.organization_id == "tenant-a"
    assert cfg.sms_template_id == "timesheet_reminder"
    assert cfg.child_env["TIMEREPORT_STDIO_TENANT"] == "tenant-a"
