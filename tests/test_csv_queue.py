from __future__ import annotations

from pathlib import Path

from time_report_agent.csv_queue import LocalDisposition, load_queue
from tests.conftest import write_csv


def test_semicolon_padded_headers_blank_hours(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id    ;employee_name   ; telephone     ; date          ;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;\n",
    )
    result = load_queue(path, period="2026-11")
    assert result.header_error is None
    assert result.delimiter == ";"
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.disposition is LocalDisposition.VALID
    assert row.employee_id == "emp-1"
    assert row.employee_name == "Synthetic Person"
    assert row.telephone_e164 == "+46701234567"
    assert row.missing_hours is None


def test_comma_delimiter(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id,employee_name,telephone,date,hours\n"
        "emp-1,Synthetic Person,+46701234567,2026-11-05,\n",
    )
    result = load_queue(path, period="2026-11")
    assert result.delimiter == ","
    assert result.rows[0].disposition is LocalDisposition.VALID


def test_utf8_bom(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;\n",
        encoding="utf-8-sig",
    )
    result = load_queue(path, period="2026-11")
    assert result.encoding == "utf-8-sig"
    assert result.rows[0].disposition is LocalDisposition.VALID


def test_hours_zero_skipped(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;0\n",
    )
    row = load_queue(path, period="2026-11").rows[0]
    assert row.disposition is LocalDisposition.SKIPPED_NO_MISSING_HOURS


def test_hours_positive_is_missing_hours(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;8\n",
    )
    row = load_queue(path, period="2026-11").rows[0]
    assert row.disposition is LocalDisposition.VALID
    assert row.missing_hours == 8.0


def test_hours_negative_invalid(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;-1\n",
    )
    row = load_queue(path, period="2026-11").rows[0]
    assert row.disposition is LocalDisposition.INVALID


def test_hours_text_invalid(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;eight\n",
    )
    row = load_queue(path, period="2026-11").rows[0]
    assert row.disposition is LocalDisposition.INVALID


def test_date_outside_period(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-10-05;\n",
    )
    row = load_queue(path, period="2026-11").rows[0]
    assert row.disposition is LocalDisposition.INVALID
    assert "period" in (row.reason or "")


def test_duplicate_employee_ids(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;\n"
        "emp-1;Synthetic Person;0701234567;2026-11-06;\n",
    )
    rows = load_queue(path, period="2026-11").rows
    assert all(r.disposition is LocalDisposition.INVALID for r in rows)
    assert all("duplicate" in (r.reason or "") for r in rows)


def test_blank_name_invalid(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;;0701234567;2026-11-05;\n",
    )
    row = load_queue(path, period="2026-11").rows[0]
    assert row.disposition is LocalDisposition.INVALID


def test_bad_phone_invalid(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;12;2026-11-05;\n",
    )
    row = load_queue(path, period="2026-11").rows[0]
    assert row.disposition is LocalDisposition.INVALID


def test_missing_period_rejected(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "q.csv",
        "employee_id;employee_name;telephone;date;hours\n"
        "emp-1;Synthetic Person;0701234567;2026-11-05;\n",
    )
    result = load_queue(path, period="2026-13")
    assert result.header_error is not None
