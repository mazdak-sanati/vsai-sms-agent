# Plan — Phase 2 time-report notification CLI

**Status:** approved and implemented (M1–M6). Live 46elks send still requires a separate explicit approval.

**Date:** 2026-11-20 Europe/Stockholm

This repository is the Phase 2 client only. The Phase 1 server at `C:\Users\Mazda\Documents\vectorsynapse-timereport-mcp-server` stays unchanged. Verified facts below come from that server's source and from a read-only structural pass over `queue\list.csv`. Design choices are marked as decisions.

## 1. What the first release is

A Windows command-line program. The user runs it by hand. It reads the queue CSV, asks the existing MCP server whether those employees are incomplete, and sends at most one SMS per employee per local day when the user passes `--send`.

No LLM. No scheduler. No second SMS provider. No edits to the MCP repository.

## 2. Verified MCP integration

Launch command, from `pyproject.toml` and `src/timereport_mcp/server.py`:

```text
timereport-mcp --transport stdio
timereport-mcp --transport streamable-http
```

Python requirement is 3.12+. Default transport is stdio. There is no Cursor MCP config in this workspace that already launches the server. The CLI will start it.

### stdio (decision: use this)

One process is one tenant. Startup requires `TIMEREPORT_STDIO_TENANT` to name a tenant in `TIMEREPORT_CONFIG`. The process binds the first client configured for that tenant. Tool arguments do not carry `tenant_id`.

### HTTP (not used in v1)

`streamable-http` on `127.0.0.1:8000`, path `/mcp`, bearer token matched by SHA-256 to a `[[clients]]` row. Available later if one process must serve multiple tenants.

### Server environment the child process needs

| Variable | Role |
|---|---|
| `TIMEREPORT_CONFIG` | Tenant TOML. The example file is synthetic and is not a production config. |
| `TIMEREPORT_STDIO_TENANT` | Must equal this app's `ORGANIZATION_ID`. |
| `TIMEREPORT_DATA_DIR` / `DATABASE_URL` | Server-side SMS claims, suppression, and audit. |
| `SMS_PROVIDER` | `mock` until a live send is explicitly approved. |
| `SMS_LIVE_SEND` | `false` for mock. `elks` refuses to start unless this is `true` and 46elks credentials are present. |
| `ELKS_API_USERNAME`, `ELKS_API_PASSWORD`, `ELKS_FROM` | Read only by the MCP process, and only for live 46elks. This application does not read, log, or store them. |

Any environment name starting with `LLM_` makes the MCP server refuse to start. This application will not set one.

### Tools that exist

All results are `{ "ok": true, "data": ... }` or `{ "ok": false, "error": { "code", "message", "details" } }`.

| Tool | Arguments | Scope | v1 use |
|---|---|---|---|
| `list_workbooks` | none | `workbooks:read` | Resolve the single workbook, or require `WORKBOOK_ID` when several exist. |
| `inspect_timesheet` | `workbook_id` | `workbooks:read` | Optional schema check. |
| `read_timesheet_entries` | `workbook_id`, `period` (`YYYY-MM`) | `workbooks:read` | Not required for v1. The payload includes employee id, name, date, and hours. It does not include the telephone, so it cannot prove the CSV number matches the workbook number. |
| `get_reporting_status` | `workbook_id`, `period`, optional `employee_id` | `workbooks:read` | Eligibility. Statuses: `complete`, `incomplete`, `policy_review_required`, `data_review_required`. |
| `get_sms_status` | `employee_id`, `period`, optional `local_date` | `sms:read` | Claim state and suppression. |
| `send_sms` | `workbook_id`, `employee_id`, `period`, `template_id` | `sms:send` | The only send path. |
| `update_timesheet_entry` | versioned cell write | `workbooks:write` | Not called in v1. |
| `set_sms_suppression` | `employee_id`, `reason`, `active` | `sms:admin` | Not called in v1. Active suppression from `get_sms_status` still blocks a send. |

`send_sms` looks up the employee and the destination number in the configured Excel workbook. It renders the tenant template. The only allowed placeholder is `{period}`. It commits a claim before the provider call. The same tenant + employee + period + Europe/Stockholm date returns the stored claim with `replayed: true`. Provider timeout is stored as `uncertain` and is not resent that day. Unknown employees return `not_found`. A number that is not valid E.164 for the tenant region returns `sms_not_eligible`.

There is no CSV tool, no message-body argument, no missing-hours argument, and no recipient argument.

## 3. Verified queue file

`C:\Users\Mazda\Documents\time_report_agent\queue\list.csv`

| Fact | Value |
|---|---|
| Encoding | UTF-8 with BOM, CRLF |
| Delimiter | `;` |
| Size | 1 header row and 1 data row |
| Header cells, before trimming | `employee_id`, `employee_name`, `telephone`, `date`, `hours` — several cells are padded with spaces |
| Period column | absent |
| `employee_id` | one numeric token; keep the raw text, do not parse it as a float |
| `employee_name` | one two-word text value |
| `telephone` | one unquoted digit string, no leading `+`, country prefix `46` |
| `date` | one ISO date |
| `hours` | empty |

The file is timesheet-shaped. An empty `hours` cell means that dated row is unreported. It is not a positive "missing hours" quantity. Personal values stay out of docs, logs, and git. `queue/*.csv` will be gitignored when implementation starts.

## 4. Decisions

1. **Parse the CSV in this process.** The server has no CSV tool. SMS, reporting status, suppression, and idempotent delivery still go through MCP. A failed or ambiguous MCP session stops the run. There is no direct 46elks client.
2. **stdio, one tenant.** `ORGANIZATION_ID` must equal `TIMEREPORT_STDIO_TENANT`. A mismatch exits before any row is processed.
3. **`--period YYYY-MM` is required.** The date column is a day inside that month. A date outside the period is an invalid row. The current date is never used as the period.
4. **A row is eligible only when every check passes.**
   - Header names match after trim, case-sensitive: `employee_id`, `employee_name`, `telephone`, `date`, `hours`.
   - `employee_id` and `employee_name` are non-empty. Duplicate `employee_id` values in one file are conflicts and are not sent.
   - The telephone normalizes to E.164 with region `SE`.
   - `hours` is empty. A numeric value, including zero, means that day is already filled in and the row is skipped. Negative, non-numeric, or ambiguous hours are invalid and are not sent.
   - `get_reporting_status` for that `employee_id` and `--period` is `incomplete`.
   - `complete`, `policy_review_required`, and `data_review_required` do not send.
   - The employee exists in the tenant workbook. `not_found` does not send.
   - `get_sms_status` shows no active suppression and no claim already `pending`, `sent`, or `uncertain` for today.
5. **v1 SMS text is the server template.** The example sentence with first name, missing hours, and organization cannot be passed to `send_sms`. Putting `{first_name}` or `{missing_hours}` in a template is rejected by the server. This CLI will call `send_sms` with `template_id` from `SMS_TEMPLATE_ID` (default `timesheet_reminder`). Dry-run prints the eligibility counts and the template id. It does not invent a body. A richer Swedish body is a Phase 1 server change, described in section 8, and is not a reason to bypass MCP.
6. **Missing hours in the summary come from the server when status is `incomplete`:** `expected_hours - reported_hours`. That number is for the operator summary. It is not interpolated into the SMS in v1.
7. **Local ledger plus server claim.** SQLite in this repo records organization, employee id, period, Stockholm date, MCP state, provider message id, and an idempotency key. Unique constraint on organization + employee + period + local date. A second process takes a file lock. `replayed: true` is "already sent today". `uncertain` is recorded and not retried on that date. The server remains the authority that actually prevents a duplicate provider call.
8. **Send window.** Default 08:00–18:00 Europe/Stockholm, Monday–Friday, from config. Outside the window, `--send` sends nothing and exits nonzero. Dry-run still reports who would have been eligible.
9. **One workbook.** Use the only workbook from `list_workbooks`. If the tenant has several, `WORKBOOK_ID` is required.
10. **Dry-run is the default.** `--send` is the only flag that may call `send_sms`. `--dry-run` may be passed explicitly and never calls it. Tests do not pass `--send` against a live provider.
11. **Stdout identifies rows by `employee_id`.** Names and telephone numbers are not printed. Logs follow the same rule and never print tokens, passwords, or template secrets.

## 5. Command contract

```powershell
python -m time_report_agent check-config

python -m time_report_agent run --csv "C:\Users\Mazda\Documents\time_report_agent\queue\list.csv" --period 2026-11 --dry-run

python -m time_report_agent run --csv "C:\Users\Mazda\Documents\time_report_agent\queue\list.csv" --period 2026-11 --send
```

`check-config` loads `.env`, checks the tenant id, the CSV path, the period format used by `run`, and that the MCP child starts and lists the required tools. It does not call `send_sms`.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Run finished. Every processed row is sent, replayed, skipped, or invalid. No `failed` or `uncertain`. |
| 1 | Configuration, tenant mismatch, or MCP session failed closed before sending. |
| 2 | The batch finished and at least one row is `failed` or `uncertain`. |

Invalid rows do not stop later independent rows.

## 6. Configuration

`.env.example` will contain placeholders only:

- `MCP_COMMAND` — default `timereport-mcp --transport stdio`, with the server's virtualenv on `PATH` or an explicit path
- `TIMEREPORT_CONFIG`
- `ORGANIZATION_ID`
- `TIMEREPORT_STDIO_TENANT`
- `WORKBOOK_ID` — empty when the tenant has one workbook
- `SMS_TEMPLATE_ID=timesheet_reminder`
- `DEFAULT_CSV_PATH`
- `TIMEZONE=Europe/Stockholm`
- `SEND_WINDOW_START=08:00`
- `SEND_WINDOW_END=18:00`
- `LEDGER_PATH`
- `TIMEREPORT_DATA_DIR` and `DATABASE_URL` passed through to the child
- `SMS_PROVIDER=mock`

46elks variables are documented as MCP-process settings and are absent from this app's settings model. The child environment is an allowlist. This process does not print it.

The real tenant TOML, real workbook, and real token hashes stay outside git. The synthetic example config can prove `check-config` only. It does not contain the employee in `queue\list.csv`, so a dry-run against that example reports the row as not found in the workbook.

## 7. Architecture

```text
time_report_agent/
  pyproject.toml
  README.md
  .env.example
  .gitignore
  docs/plan.md
  docs/status.md
  src/time_report_agent/
    __main__.py          # python -m
    cli.py               # argparse: run, check-config
    config.py            # env load, redacted errors
    csv_queue.py         # delimiter/encoding/header mapping
    eligibility.py       # pure rules
    mcp_client.py        # stdio session, tool calls, fail closed
    ledger.py            # sqlite + lock
    report.py            # redacted summary
  tests/
    synthetic CSV fixtures
    fake MCP that records whether send_sms was called
```

Pipeline for `run`:

1. Load config. Refuse to start when `ORGANIZATION_ID` is empty or differs from `TIMEREPORT_STDIO_TENANT`.
2. Open the MCP session and require `list_workbooks`, `get_reporting_status`, `get_sms_status`, and `send_sms`. A missing tool exits 1.
3. Read the CSV from disk on every invocation.
4. Validate rows locally.
5. For each valid employee, call `get_reporting_status` and `get_sms_status`.
6. Print the redacted summary.
7. On `--send`, inside the window, take the ledger lock, insert a `pending` row, call `send_sms`, then store `sent`, `replayed`, `failed`, or `uncertain`. A crash after `pending` and before a known outcome leaves the row `uncertain` and blocks another send that day.

## 8. MCP gaps to leave for a server change

These are real limits. v1 will not pretend they are implemented.

| Need in `AGENTS.md` | What the server does | v1 behavior |
|---|---|---|
| SMS text with first name, missing hours, and organization | Template body may contain only `{period}` | Send the configured template. Record the wording limit in the README. |
| Queue is CSV | Tools read allowlisted `.xlsx` workbooks | Parse CSV locally. Send only through `send_sms`. |
| Destination from the queue | `send_sms` dials the workbook telephone | Send only when that employee id exists in the workbook. Reject a CSV number that fails E.164. The current read tools do not return the workbook number, so v1 cannot prove the two numbers match. |
| Do not send unless hours are missing | `send_sms` does not read reporting status | This CLI calls `send_sms` only after `incomplete`. |

A later Phase 1 change could allow a fixed placeholder set such as `{period}`, `{first_name}`, `{missing_hours}`, and `{organization}`, still with a length cap and no free-form body from the model. That change is out of scope here until you ask for it in the MCP repository.

## 9. Tests

Synthetic rows and an in-process fake MCP only. No live 46elks, no use of `queue\list.csv` in tests.

- Semicolon and comma, padded headers, UTF-8 BOM.
- Blank hours eligible; `0`, positive, negative, and text hours not eligible.
- Swedish numbers with `+46`, `46`, and a leading `0` normalize; short and empty numbers do not.
- Duplicate employee ids, missing period, date outside `--period`, blank name.
- Two organization ids in one config are rejected. The fake server's tenant is the only one the client will call.
- Dry-run records zero `send_sms` calls.
- `--send` calls `send_sms` with `workbook_id`, `employee_id`, `period`, and `template_id`.
- A second run the same Stockholm day does not call `send_sms` again after a `sent` or `replayed` result.
- Two overlapping processes: one send, one lock wait or unique-constraint skip.
- `uncertain` and a killed process after `pending` do not send again that day.
- The next local date can send only while the new CSV still has blank hours and the server status is still `incomplete`.
- Suppressed employees are skipped.
- Tool missing, connection failure, and tenant mismatch exit 1 with no send.
- Captured logs contain no telephone, no credential, and no raw CSV name.

## 10. Milestones

| Step | Delivered | Done when |
|---|---|---|
| M0 | This plan and `docs/status.md` | You approve it, including section 4.5 on template text |
| M1 | Package, `.gitignore`, `.env.example`, `check-config` against a fake or the synthetic server | `check-config` exits 0 without calling `send_sms` |
| M2 | CSV parser and redacted dry-run with no MCP | Tests for headers, hours, phones, duplicates |
| M3 | stdio client and capability check | Missing tool fails closed |
| M4 | Eligibility join with reporting and SMS status | Only `incomplete` and a blank hours cell can be eligible |
| M5 | Ledger, lock, window, `--send` | Dry-run never sends; same-day replay does not send again |
| M6 | README with the PowerShell commands and the template limitation | A reviewer can run the mock path from the README |

## 11. Acceptance

After implementation, this PowerShell dry-run reads the queue and prints counts by employee id without sending:

```powershell
python -m time_report_agent run --csv "C:\Users\Mazda\Documents\time_report_agent\queue\list.csv" --period 2026-11 --dry-run
```

`--send` sends only through `send_sms`, at most once per employee, organization, and period per Stockholm day, and only for a blank-hours row whose server status is `incomplete`. A live 46elks send still requires a separate explicit approval and `SMS_PROVIDER=elks` on the MCP process.

## 12. Approval requested

Implementation waits for an explicit request to implement this plan. The decision to confirm with that approval is section 4.5: v1 uses the server template and does not add a custom message body.
