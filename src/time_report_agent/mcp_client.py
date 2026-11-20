"""MCP client adapter. Fail closed when tools or session are unavailable."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

from time_report_agent.config import REQUIRED_MCP_TOOLS, AppConfig
from time_report_agent.eligibility import SmsStatusView


class McpClientError(RuntimeError):
    """Raised when the MCP session cannot be used safely."""


@dataclass(frozen=True, slots=True)
class ToolResultView:
    ok: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


class TimeReportMcp(Protocol):
    async def list_tool_names(self) -> set[str]: ...

    async def list_workbooks(self) -> ToolResultView: ...

    async def get_sms_status(
        self,
        *,
        employee_id: str,
        period: str,
        local_date: str | None = None,
    ) -> ToolResultView: ...

    async def send_sms(
        self,
        *,
        workbook_id: str,
        employee_id: str,
        period: str,
        template_id: str,
    ) -> ToolResultView: ...


def parse_tool_payload(payload: Any) -> ToolResultView:
    if payload is None:
        return ToolResultView(ok=False, error_code="empty_result", error_message="empty tool result")
    if not isinstance(payload, dict):
        return ToolResultView(
            ok=False,
            error_code="invalid_result",
            error_message="tool result was not an object",
        )
    if payload.get("ok") is True:
        data = payload.get("data")
        return ToolResultView(ok=True, data=data if isinstance(data, dict) else {})
    error = payload.get("error") or {}
    if not isinstance(error, dict):
        error = {}
    return ToolResultView(
        ok=False,
        error_code=str(error.get("code") or "unknown_error"),
        error_message=str(error.get("message") or "tool returned ok=false"),
        data=error.get("details") if isinstance(error.get("details"), dict) else None,
    )


def require_tools(names: set[str]) -> None:
    missing = sorted(REQUIRED_MCP_TOOLS - names)
    if missing:
        raise McpClientError(f"MCP server is missing required tools: {missing}")


def resolve_workbook_id(
    listed: ToolResultView,
    configured: str | None,
) -> str:
    if not listed.ok or listed.data is None:
        raise McpClientError(
            f"list_workbooks failed: {listed.error_code} {listed.error_message}"
        )
    workbooks = listed.data.get("workbooks") or []
    if not isinstance(workbooks, list) or not workbooks:
        raise McpClientError("list_workbooks returned no workbooks")
    ids = [
        str(item.get("workbook_id"))
        for item in workbooks
        if isinstance(item, dict) and item.get("workbook_id")
    ]
    if configured:
        if configured not in ids:
            raise McpClientError(
                f"WORKBOOK_ID {configured!r} is not in the tenant workbook list"
            )
        return configured
    if len(ids) != 1:
        raise McpClientError(
            "tenant has multiple workbooks; set WORKBOOK_ID explicitly"
        )
    return ids[0]


def sms_view_from_result(result: ToolResultView, *, employee_id: str) -> SmsStatusView:
    if not result.ok or result.data is None:
        raise McpClientError(
            f"get_sms_status failed: {result.error_code} {result.error_message}"
        )
    suppression = result.data.get("suppression")
    claim = result.data.get("claim")
    suppressed = False
    reason: str | None = None
    if isinstance(suppression, dict) and suppression.get("active") is True:
        suppressed = True
        reason = str(suppression.get("reason") or "unknown")
    claim_state = None
    if isinstance(claim, dict) and claim.get("state") is not None:
        claim_state = str(claim.get("state"))
    return SmsStatusView(
        employee_id=employee_id,
        suppressed=suppressed,
        suppression_reason=reason,
        claim_state=claim_state,
    )


class StdioMcpClient:
    """MCP client that launches the TimeReport server over stdio."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._client: Any | None = None

    async def __aenter__(self) -> StdioMcpClient:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        command = self._config.mcp_command[0]
        args = list(self._config.mcp_command[1:])
        params = StdioServerParameters(
            command=command,
            args=args,
            env=self._config.child_env,
            cwd=str(self._config.mcp_cwd) if self._config.mcp_cwd else None,
        )
        self._client = Client(params)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    async def list_tool_names(self) -> set[str]:
        assert self._client is not None
        result = await self._client.list_tools()
        tools = getattr(result, "tools", result)
        names: set[str] = set()
        for tool in tools:
            name = getattr(tool, "name", None)
            if name:
                names.add(str(name))
        return names

    async def _call(self, name: str, arguments: dict[str, Any]) -> ToolResultView:
        assert self._client is not None
        try:
            result = await self._client.call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 — fail closed
            raise McpClientError(f"tool call {name} failed: {exc}") from exc
        payload = getattr(result, "structured_content", None)
        if payload is None:
            # Fallback: some SDK shapes put JSON in content text.
            content = getattr(result, "content", None)
            if isinstance(content, list) and content:
                first = content[0]
                text = getattr(first, "text", None)
                if isinstance(text, str) and text.strip().startswith("{"):
                    import json

                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        payload = None
        return parse_tool_payload(payload)

    async def list_workbooks(self) -> ToolResultView:
        return await self._call("list_workbooks", {})

    async def get_sms_status(
        self,
        *,
        employee_id: str,
        period: str,
        local_date: str | None = None,
    ) -> ToolResultView:
        args: dict[str, Any] = {"employee_id": employee_id, "period": period}
        if local_date is not None:
            args["local_date"] = local_date
        return await self._call("get_sms_status", args)

    async def send_sms(
        self,
        *,
        workbook_id: str,
        employee_id: str,
        period: str,
        template_id: str,
    ) -> ToolResultView:
        return await self._call(
            "send_sms",
            {
                "workbook_id": workbook_id,
                "employee_id": employee_id,
                "period": period,
                "template_id": template_id,
            },
        )


@dataclass
class FakeMcpClient:
    """In-process fake for tests. Records whether send_sms was called."""

    tool_names: set[str] = field(
        default_factory=lambda: set(REQUIRED_MCP_TOOLS)
    )
    workbooks: list[dict[str, Any]] = field(
        default_factory=lambda: [{"workbook_id": "wb-1", "label": "Test"}]
    )
    sms: dict[str, dict[str, Any]] = field(default_factory=dict)
    send_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    send_calls: list[dict[str, Any]] = field(default_factory=list)
    fail_connect: bool = False

    async def list_tool_names(self) -> set[str]:
        if self.fail_connect:
            raise McpClientError("connection failed")
        return set(self.tool_names)

    async def list_workbooks(self) -> ToolResultView:
        return ToolResultView(ok=True, data={"workbooks": list(self.workbooks)})

    async def get_sms_status(
        self,
        *,
        employee_id: str,
        period: str,
        local_date: str | None = None,
    ) -> ToolResultView:
        payload = self.sms.get(
            employee_id,
            {"employee_id": employee_id, "period": period, "claim": None, "suppression": None},
        )
        return ToolResultView(ok=True, data=dict(payload))

    async def send_sms(
        self,
        *,
        workbook_id: str,
        employee_id: str,
        period: str,
        template_id: str,
    ) -> ToolResultView:
        call = {
            "workbook_id": workbook_id,
            "employee_id": employee_id,
            "period": period,
            "template_id": template_id,
        }
        self.send_calls.append(call)
        result = self.send_results.get(
            employee_id,
            {
                "ok": True,
                "data": {
                    "employee_id": employee_id,
                    "period": period,
                    "state": "sent",
                    "provider": "mock",
                    "provider_message_id": f"mock-{len(self.send_calls)}",
                    "replayed": False,
                },
            },
        )
        if result.get("ok") is False:
            error = result.get("error") or {}
            return ToolResultView(
                ok=False,
                error_code=str(error.get("code") or "failed"),
                error_message=str(error.get("message") or "send failed"),
            )
        return ToolResultView(ok=True, data=dict(result.get("data") or {}))


@asynccontextmanager
async def open_stdio_client(config: AppConfig) -> AsyncIterator[StdioMcpClient]:
    client = StdioMcpClient(config)
    async with client:
        names = await client.list_tool_names()
        require_tools(names)
        yield client


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)
