"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from time_report_agent.config import ConfigError, load_config
from time_report_agent.csv_queue import validate_period
from time_report_agent.eligibility import Outcome
from time_report_agent.ledger import Ledger
from time_report_agent.mcp_client import (
    McpClientError,
    open_stdio_client,
    require_tools,
    run_async,
)
from time_report_agent.pipeline import run_pipeline
from time_report_agent.report import RunSummary


EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_PARTIAL = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="time_report_agent",
        description=(
            "Read a reporting queue CSV and optionally send SMS reminders "
            "through the TimeReport MCP server."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-config", help="Validate config and MCP tool discovery")
    check.add_argument("--env-file", type=Path, default=None)

    run = sub.add_parser("run", help="Process the queue (dry-run by default)")
    run.add_argument("--env-file", type=Path, default=None)
    run.add_argument("--csv", type=Path, default=None)
    run.add_argument("--period", required=True, help="Reporting period YYYY-MM")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without calling send_sms (default)",
    )
    mode.add_argument(
        "--send",
        action="store_true",
        help="Send eligible SMS immediately through the MCP server",
    )
    mode.add_argument(
        "--confirm",
        action="store_true",
        help="Show a preview, then ask in the terminal before sending",
    )
    return parser


def _exit_for_summary(summary: RunSummary) -> int:
    if summary.config_error:
        return EXIT_CONFIG
    if summary.has_failed_or_uncertain():
        return EXIT_PARTIAL
    return EXIT_OK


def _ask_to_send(eligible_count: int) -> bool:
    """Prompt on a TTY. Returns False if declined or stdin is not interactive."""
    if eligible_count <= 0:
        print("No eligible recipients to send.")
        return False
    if not sys.stdin.isatty():
        print(
            "confirm error: stdin is not interactive; re-run in a terminal "
            "or use --send after reviewing a dry-run",
            file=sys.stderr,
        )
        return False
    prompt = (
        f"Send SMS to {eligible_count} eligible recipient(s) via MCP? [y/N]: "
    )
    try:
        answer = input(prompt)
    except EOFError:
        print("Send cancelled.")
        return False
    if answer.strip().lower() in {"y", "yes"}:
        return True
    print("Send cancelled.")
    return False


async def _check_config_async(env_file: Path | None) -> int:
    try:
        config = load_config(env_file=env_file)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if not config.timereport_config.is_file():
        print(
            f"config error: TIMEREPORT_CONFIG not found: {config.timereport_config}",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    print(f"organization={config.organization_id}")
    print(f"stdio_tenant={config.stdio_tenant}")
    print(f"mcp_command={' '.join(config.mcp_command)}")
    print(f"timereport_config={config.timereport_config}")
    print(f"default_csv={config.default_csv_path}")
    print(f"ledger={config.ledger_path}")
    print(f"template={config.sms_template_id}")
    print(f"timezone={config.timezone}")
    print(f"sms_provider={config.child_env.get('SMS_PROVIDER', 'mock')}")
    print(f"sms_live_send={config.child_env.get('SMS_LIVE_SEND', 'false')}")

    try:
        async with open_stdio_client(config) as client:
            names = await client.list_tool_names()
            require_tools(names)
            listed = await client.list_workbooks()
            if not listed.ok:
                print(
                    f"mcp error: list_workbooks failed: {listed.error_code}",
                    file=sys.stderr,
                )
                return EXIT_CONFIG
            workbooks = (listed.data or {}).get("workbooks") or []
            print(f"mcp_tools=ok ({len(names)} tools)")
            print(f"workbooks={len(workbooks)}")
            print("send_sms=not called")
    except McpClientError as exc:
        print(f"mcp error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001
        print(f"mcp error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    return EXIT_OK


async def _run_async(args: argparse.Namespace) -> int:
    period_err = validate_period(args.period)
    if period_err:
        print(f"config error: {period_err}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        config = load_config(env_file=args.env_file)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    csv_path = args.csv or config.default_csv_path
    if not csv_path.is_file():
        print(f"config error: CSV not found: {csv_path}", file=sys.stderr)
        return EXIT_CONFIG

    send_now = bool(args.send)
    confirm = bool(args.confirm)
    ledger = Ledger(config.ledger_path)
    now = datetime.now(ZoneInfo(config.timezone))

    try:
        async with open_stdio_client(config) as client:
            # Always show a preview first for --confirm.
            preview = await run_pipeline(
                config,
                client,
                csv_path=csv_path,
                period=args.period,
                send=False,
                now=now,
                ledger=ledger,
            )
            if confirm:
                print(preview.format())
                print("")
                eligible = sum(
                    1 for c in preview.candidates if c.outcome is Outcome.ELIGIBLE
                )
                if preview.config_error:
                    return _exit_for_summary(preview)
                if not _ask_to_send(eligible):
                    return EXIT_OK
                summary = await run_pipeline(
                    config,
                    client,
                    csv_path=csv_path,
                    period=args.period,
                    send=True,
                    now=now,
                    ledger=ledger,
                )
                print("")
                print(summary.format())
                return _exit_for_summary(summary)

            summary = await run_pipeline(
                config,
                client,
                csv_path=csv_path,
                period=args.period,
                send=send_now,
                now=now,
                ledger=ledger,
            )
    except McpClientError as exc:
        print(f"mcp error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001
        print(f"mcp error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    print(summary.format())
    return _exit_for_summary(summary)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check-config":
        return run_async(_check_config_async(args.env_file))
    if args.command == "run":
        return run_async(_run_async(args))
    parser.error(f"unknown command {args.command}")
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
