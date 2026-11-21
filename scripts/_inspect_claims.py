"""One-off claim inspector — prints redacted claim rows only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

PATHS = [
    Path(r"C:\Users\Mazda\Documents\time_report_agent\data\send_ledger.db"),
    Path(r"C:\Users\Mazda\Documents\time_report_agent\data\mcp-send\timereport.db"),
]


def redact(value: object) -> object:
    if value is None:
        return None
    text = str(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"***{digits[-4:]}"
    return value


def main() -> None:
    for path in PATHS:
        print("===", path.name)
        if not path.exists():
            print("  missing")
            continue
        con = sqlite3.connect(path)
        cur = con.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        print("tables", tables)
        for table in tables:
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
            print(table, cols)
            rows = cur.execute(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 8"
            ).fetchall()
            for row in rows:
                print(" ", [redact(v) for v in row])
        con.close()


if __name__ == "__main__":
    main()
