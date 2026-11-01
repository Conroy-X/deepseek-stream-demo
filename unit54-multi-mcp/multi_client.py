"""Unit 54 · 多 MCP 客户端。

核心：同时连 N 个 stdio 子进程，给工具名加命名空间前缀（`math.add` / `text.upper` / `db.query`）。
部分失败降级：一个 server 起不来时，剩下仍可用。
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVERS_DIR = Path(__file__).resolve().parent / "servers"


@dataclass
class ToolResult:
    name: str
    ok: bool
    text: str
    raised: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "ok": self.ok, "text": self.text}
        if self.raised:
            out["raised"] = self.raised
        return out


@dataclass
class ServerStatus:
    name: str
    path: str
    connected: bool
    error: str | None = None
    tool_count: int = 0


class MultiMCPClient:
    """同时连多个 stdio 子进程。工具名加前缀防冲突。"""

    def __init__(self, specs: list[tuple[str, str]] | None = None) -> None:
        # specs: [(namespace, server_filename), ...]
        self.specs = specs or [("math", "math_server.py"), ("text", "text_server.py"), ("db", "db_server.py")]
        self._stack: AsyncExitStack | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, dict[str, Any]] = {}  # namespaced_name → {namespace, raw_name, schema}
        self.statuses: list[ServerStatus] = []

    async def __aenter__(self) -> "MultiMCPClient":
        self._stack = AsyncExitStack()
        self.statuses = []
        for ns, fname in self.specs:
            path = SERVERS_DIR / fname
            status = ServerStatus(name=ns, path=str(path), connected=False)
            try:
                params = StdioServerParameters(command=sys.executable, args=[str(path)])
                r, w = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(r, w))
                await session.initialize()
                listed = await session.list_tools()
                self._sessions[ns] = session
                for t in listed.tools:
                    namespaced = f"{ns}.{t.name}"
                    self._tools[namespaced] = {
                        "namespace": ns,
                        "raw_name": t.name,
                        "name": namespaced,
                        "description": t.description or "",
                        "input_schema": t.input_schema,
                    }
                status.connected = True
                status.tool_count = len(listed.tools)
            except Exception as e:
                status.error = f"{type(e).__name__}: {e}"
            self.statuses.append(status)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack:
            await self._stack.aclose()
        self._stack = None
        self._sessions.clear()
        self._tools.clear()

    # ── 查询 ────────────────────────────────────────────────────────────────

    @property
    def connected_count(self) -> int:
        return sum(1 for s in self.statuses if s.connected)

    @property
    def failed_count(self) -> int:
        return sum(1 for s in self.statuses if not s.connected)

    def list_tools(self) -> list[dict[str, Any]]:
        """返回所有工具，名字带前缀。"""
        return sorted(self._tools.values(), key=lambda t: t["name"])

    def list_tools_by_ns(self) -> dict[str, list[dict[str, Any]]]:
        """按命名空间分组。"""
        out: dict[str, list[dict[str, Any]]] = {}
        for t in self._tools.values():
            out.setdefault(t["namespace"], []).append(t)
        return out

    def has_conflicts(self) -> dict[str, list[str]]:
        """检查原始工具名是否有跨 namespace 冲突。

        math 和 db 都有 add → 冲突。冲突必须靠前缀隔离。
        """
        raw_to_ns: dict[str, list[str]] = {}
        for t in self._tools.values():
            raw_to_ns.setdefault(t["raw_name"], []).append(t["namespace"])
        return {raw: sorted(nses) for raw, nses in raw_to_ns.items() if len(nses) > 1}

    async def call_tool(self, namespaced_name: str, args: dict[str, Any] | None = None) -> ToolResult:
        meta = self._tools.get(namespaced_name)
        if meta is None:
            return ToolResult(name=namespaced_name, ok=False, text=f"未知工具：{namespaced_name}")
        ns = meta["namespace"]
        session = self._sessions.get(ns)
        if session is None:
            return ToolResult(name=namespaced_name, ok=False, text=f"namespace {ns} 未连接", raised="disconnected")
        try:
            raw = await session.call_tool(meta["raw_name"], args or {})
            text = ""
            if raw.content:
                text = getattr(raw.content[0], "text", "") or str(raw.content[0])
            return ToolResult(name=namespaced_name, ok=not bool(raw.is_error), text=text)
        except Exception as e:
            return ToolResult(name=namespaced_name, ok=False, text=f"传输层失败：{type(e).__name__}: {e}", raised=type(e).__name__)


def route_by_keyword(question: str, tools_by_ns: dict[str, list[dict[str, Any]]]) -> str | None:
    """根据问题关键词路由到 namespace。返回 namespace 或 None。"""
    q = question.lower()
    if any(k in q for k in ["加", "平方", "数学", "乘"]):
        return "math" if "math" in tools_by_ns else None
    if any(k in q for k in ["大写", "反转", "字符串", "文本"]):
        return "text" if "text" in tools_by_ns else None
    if any(k in q for k in ["库存", "sku", "商品"]):
        return "db" if "db" in tools_by_ns else None
    return None