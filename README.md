# time_report_agent

Phase 2 **interactive SMS agent** for Vector Synapse.

It waits in your terminal, uses a **local Ollama** model (`gemma4:e4b` by default), reads `queue\list.csv` to decide who needs a reminder and what the SMS should say, and sends only through the existing TimeReport MCP server after you confirm with `y/N`.

This repo does **not** reimplement 46elks. It does **not** auto-send on a schedule.

MCP server:

`C:\Users\Mazda\Documents\vectorsynapse-timereport-mcp-server`

## How the chat agent works

1. You start `chat` — the agent stays open and waits for prompts.
2. You say something like: `send sms to employees`
3. The model calls `list_sms_candidates` (reads `list.csv`).
4. It shows who is eligible and the SMS preview text.
5. If you ask it to send, it calls `send_sms_to_candidates`, which still asks **you** `y/N` in the terminal before MCP `send_sms`.

Deterministic one-shot commands (`run --dry-run` / `--confirm` / `--send`) remain available.

## Prerequisites

1. Python 3.12+
2. Ollama installed and running, with model pulled:
   ```powershell
   ollama pull gemma4:e4b
   ollama serve
   ```
3. Phase 1 MCP server install (`timereport-mcp` reachable via `MCP_COMMAND`)
4. Local tenant/workbook configured (see `config/local_tenant.toml` / `workbooks\local`)
5. `queue\list.csv` present

## Setup

```powershell
cd C:\Users\Mazda\Documents\time_report_agent
.\.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
# Edit .env — or use tests\env_local.send.env for local MCP + SMS settings
```

CSV headers after trim: `employee_id`, `employee_name`, `telephone`, `date`, `hours`.

## Start the waiting agent

```powershell
.\.venv\Scripts\python.exe -m time_report_agent chat `
  --env-file tests\env_local.send.env `
  --period 2026-11
```

Example conversation:

```text
You> send sms to employees
Agent> ...lists eligible people and SMS preview...
You> yes, send them
[tool] send_sms_to_candidates(...)
Send SMS via MCP to 1 eligible recipient(s)? [y/N]: y
```

While it works you should see traces like:

```text
[agent] user turn accepted ...
[ollama] requesting model=gemma4:e4b ...
[ollama] done in 1.2s ...
[tool] start list_sms_candidates(...)
[tool] done list_sms_candidates: ...
Agent> ...
```

If you see nothing after `You>`, wait for `[ollama] requesting` — the first model load can take a bit. `OLLAMA_THINK=false` (default) keeps turns faster.

Type `exit` to stop.

## One-shot CLI (no LLM)

```powershell
python -m time_report_agent run --env-file tests\env_local.send.env --period 2026-11 --dry-run
python -m time_report_agent run --env-file tests\env_local.send.env --period 2026-11 --confirm
```

## Ollama settings

In `.env` / env file:

```env
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e4b
DEFAULT_PERIOD=2026-11
```

These stay in the agent process. They are **not** sent to the MCP server (`LLM_*` keys are rejected by MCP; do not use that prefix for Ollama).

## SMS notes

- Eligibility, recipient phone, and message text come from **list.csv** (agent-composed).
- MCP `send_sms` is a delivery tool only: the agent passes `to` + `message` (+ `employee_id`/`period` for idempotency).
- Same-day dedupe applies (local ledger + MCP claims).
- Keep `SMS_PROVIDER=mock` until you intentionally enable Elks live send.

## Tests

```powershell
pytest
```

Tests use synthetic CSV + a fake MCP client. They do not call Ollama or send real SMS.
