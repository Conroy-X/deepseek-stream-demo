"""Unit 52 · MCP 客户端封装（stdio）。

复用 unit49 的 MCPClient 模板，给 main.py 用。
"""

from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PATH = Path(__file__).resolve().parent / "server.py"


@dataclass
class ToolResult:
    name: str
    ok: bool
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "text": self.text}


class MCPClient:
    def __init__(self, server_path: Path | str | None = None) -> None:
        self.server_path = Path(server_path or SERVER_PATH)
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.init_result: Any = None

    async def __aenter__(self) -> "MCPClient":
        self._stack = AsyncExitStack()
        params = StdioServerParameters(command=sys.executable, args=[str(self.server_path)])
        r, w = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(r, w))
        self.init_result = await self.session.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self.session = None

    @property
    def server_name(self) -> str:
        return self.init_result.server_info.name if self.init_result else ""

    @property
    def protocol_version(self) -> str:
        return self.init_result.protocol_version if self.init_result else ""

    async def list_tools(self) -> list[dict[str, Any]]:
        listed = await self.session.list_tools()
        out = []
        for t in listed.tools:
            out.append({
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.input_schema,
            })
        return out

    async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        try:
            raw = await self.session.call_tool(name, args or {})
            text = ""
            if raw.content:
                text = getattr(raw.content[0], "text", "") or str(raw.content[0])
            return ToolResult(name=name, ok=not bool(raw.is_error), text=text)
        except Exception as e:
            return ToolResult(name=name, ok=False, text=f"传输层失败：{type(e).__name__}: {e}")