"""Clear today's local send claims so a traced resend can run."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DAY = datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
LEDGER = Path(r"C:\Users\Mazda\Documents\time_report_agent\data\send_ledger.db")
MCP_DB = Path(r"C:\Users\Mazda\Documents\time_report_agent\data\mcp-send\timereport.db")


def main() -> None:
    print("clearing local_date=", DAY)
    con = sqlite3.connect(LEDGER)
    n = con.execute(
        "DELETE FROM send_ledger WHERE local_date = ? AND organization_id = ?",
        (DAY, "local"),
    ).rowcount
    con.commit()
    con.close()
    print("ledger deleted", n)

    con = sqlite3.connect(MCP_DB)
    n = con.execute(
        "DELETE FROM sms_claims WHERE local_date = ? AND tenant_id = ?",
        (DAY, "local"),
    ).rowcount
    con.commit()
    con.close()
    print("mcp claims deleted", n)


if __name__ == "__main__":
    main()
