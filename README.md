# time_report_agent

Phase 2 Windows CLI that reads an employee reporting queue CSV and, when you pass `--send`, asks the existing TimeReport MCP server to send at most one SMS reminder per employee per local day.

This repository does **not** implement SMS delivery itself. It does not call 46elks. It does not schedule background jobs. It does not use an LLM.

Phase 1 MCP server (unchanged):

`C:\Users\Mazda\Documents\vectorsynapse-timereport-mcp-server`

## What it does

1. Loads `.env` / CLI flags.
2. Starts the MCP server over **stdio** for one tenant (`ORGANIZATION_ID` must equal `TIMEREPORT_STDIO_TENANT`).
3. Reads `queue\list.csv` (or `--csv`) locally every run.
4. Treats an **empty `hours` cell** as “this day is unreported”.
5. Asks MCP `get_reporting_status` and only continues when status is `incomplete`.
6. Default mode is **dry-run** (no `send_sms`).
7. With `--send`, calls MCP `send_sms` with `workbook_id`, `employee_id`, `period`, and `template_id`, then records the outcome in a local SQLite ledger.

## SMS text limitation (v1)

The MCP `send_sms` tool does **not** accept a custom message body. It sends the tenant template, and that template may contain only `{period}`.

So this CLI cannot send:

`Hej {first_name}! Det saknas {missing_hours} timmar...`

until the Phase 1 server template contract is extended. Dry-run prints eligibility by `employee_id` and the configured `SMS_TEMPLATE_ID` (default `timesheet_reminder`).

The destination number comes from the Excel workbook configured in the MCP tenant file, not from a free-form SMS recipient argument.

## Prerequisites

1. Python 3.12+.
2. Working Phase 1 install: `pip install -e ".[dev]"` in the MCP server repo (or `timereport-mcp` on `PATH`).
3. Tenant TOML + workbook for the organization you will message. The synthetic example config is fine for `check-config`, but it will not contain your real queue employees.
4. `SMS_PROVIDER=mock` until you explicitly approve a live send.

## Setup

```powershell
cd C:\Users\Mazda\Documents\time_report_agent
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"

# Ensure the MCP server package is importable / on PATH, e.g.:
# & C:\Users\Mazda\Documents\vectorsynapse-timereport-mcp-server\.venv\Scripts\Activate.ps1
# or set MCP_COMMAND to the full path of timereport-mcp

copy .env.example .env
# Edit .env: ORGANIZATION_ID, TIMEREPORT_STDIO_TENANT, TIMEREPORT_CONFIG, MCP_COMMAND, paths
```

Put the queue file at:

`C:\Users\Mazda\Documents\time_report_agent\queue\list.csv`

Expected headers after trim (semicolon or comma): `employee_id`, `employee_name`, `telephone`, `date`, `hours`.

## Commands

```powershell
python -m time_report_agent check-config

# No SMS is sent.
python -m time_report_agent run --env-file tests\env_local.send.env --period 2026-11 --dry-run

# Preview, then ask in the terminal before sending (recommended).
python -m time_report_agent run --env-file tests\env_local.send.env --period 2026-11 --confirm

# Send immediately without a prompt (mock or live depending on env).
python -m time_report_agent run --env-file tests\env_local.send.env --period 2026-11 --send
```

For a real 46elks message, edit `tests\env_local.send.env`: set `SMS_PROVIDER=elks`, `SMS_LIVE_SEND=true`, and the three `ELKS_*` values. Then use `--confirm`.

`--period` is required. The CLI never guesses the period from today's date.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Finished without failed/uncertain rows or config errors |
| 1 | Config / tenant / MCP session failure |
| 2 | Batch finished with at least one `failed` or `uncertain` row |

## Safety

- Dry-run never calls `send_sms`.
- Same local calendar day (Europe/Stockholm): at most one successful/in-flight claim per organization + employee + period in the local ledger; the MCP server also enforces its own daily claim.
- Outside the send window (default 08:00–18:00 weekdays), `--send` does not send.
- Stdout identifies rows by `employee_id` only; names and phone numbers are not printed.
- `.env`, queue CSVs, and ledger databases are gitignored.

## Tests

```powershell
pytest
```

Tests use synthetic CSV rows and an in-process fake MCP client. They never send real SMS and never read your personal `queue\list.csv`.

## Docs

- `docs/plan.md` — approved architecture and MCP contract notes
- `docs/status.md` — progress log
- `AGENTS.md` — project mission for coding agents
