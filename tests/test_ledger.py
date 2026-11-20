from __future__ import annotations

from datetime import date
from pathlib import Path

from time_report_agent.ledger import Ledger


def test_unique_daily_claim(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.db")
    day = date(2026, 11, 20)
    assert ledger.try_insert_pending(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=day,
    )
    ledger.finalize(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=day,
        status="sent",
        provider_message_id="mock-1",
    )
    assert ledger.blocks_send(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=day,
    )
    assert not ledger.try_insert_pending(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=day,
    )


def test_uncertain_blocks_retry(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.db")
    day = date(2026, 11, 20)
    assert ledger.try_insert_pending(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=day,
    )
    ledger.finalize(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=day,
        status="uncertain",
    )
    assert ledger.blocks_send(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=day,
    )


def test_new_day_allows_send(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.db")
    assert ledger.try_insert_pending(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=date(2026, 11, 20),
    )
    ledger.finalize(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=date(2026, 11, 20),
        status="sent",
    )
    assert ledger.try_insert_pending(
        organization_id="tenant-a",
        employee_id="emp-1",
        period="2026-11",
        local_date=date(2026, 11, 21),
    )
