"""Unit 49 · 一个够用的 MCP 客户端封装（mcp 2.x）。

`stdio_client` 负责起子进程 + 接管道，`ClientSession` 负责握手和收发。
这个文件把它们包成一个能反复用的小类，后面 Unit 50–54 都会抄这份结构。

必须记住的三条 2.x 事实（都来自实测，不是猜的）：

  1. 响应对象的属性全是**蛇形**：`is_error` / `input_schema` / `server_info`，
     而线上报文仍然是驼峰（`isError` / `inputSchema`）。pydantic 在中间翻译。
  2. `anyio.run()`，不要 `asyncio.run()` —— SDK 内部用 anyio task group，混用会炸。
  3. 工具不存在 / 参数不合法 / 工具内部抛异常，`call_tool()` **一律不抛**，都是 `is_error=True`。
     所以下面的 try/except 不是用来接 ToolError 的 —— 它是接**传输层**故障
     （子进程死了、管道断了、协议帧坏了）。这两类要分开对待。
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PATH = Path(__file__).resolve().parent / "server.py"


@dataclass
class ToolResult:
    """把 SDK 的三四种返回形状统一成一个，方便上层和页面消费。"""

    name: str
    ok: bool
    text: str
    structured: Any = None
    raised: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "ok": self.ok, "text": self.text}
        if self.structured is not None:
            out["structured"] = self.structured
        if self.raised:
            out["raised"] = self.raised
        return out


class MCPClient:
    """stdio 客户端。用 `async with` 管理生命周期。"""

    def __init__(self, server_path: Path | str | None = None) -> None:
        self.server_path = Path(server_path or SERVER_PATH)
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.init_result: Any = None

    async def __aenter__(self) -> "MCPClient":
        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            # 必须绝对路径。写 "python" 会拿到系统解释器（没装 mcp），
            # 症状是子进程一启动就退出，父进程只看到 EOF。用 sys.executable 最省事。
            command=sys.executable,
            args=[str(self.server_path)],
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        self.init_result = await self.session.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self.session = None

    # ── 能力 ────────────────────────────────────────────────────────────────

    @property
    def server_name(self) -> str:
        return self.init_result.server_info.name if self.init_result else ""

    @property
    def protocol_version(self) -> str:
        return self.init_result.protocol_version if self.init_result else ""

    def capabilities(self) -> dict[str, Any]:
        if not self.init_result:
            return {}
        return self.init_result.capabilities.model_dump(exclude_none=True)

    # ── 三类能力 ────────────────────────────────────────────────────────────

    async def list_tools(self) -> list[dict[str, Any]]:
        assert self.session is not None
        listed = await self.session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.input_schema,
                "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else {},
            }
            for t in listed.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """把 SDK 的返回统一成 `ToolResult`，并且不让**传输层**故障逃出去。

        注意分工：业务失败（未知工具、参数不合法、工具内部抛异常）SDK 已经处理成
        `is_error=True` 了，这里只是转个手。真正的 `try/except` 是防管道断、子进程死
        这类传输故障 —— 那才是会真的抛出来、并且会把整个 Agent loop 带崩的东西。
        """
        assert self.session is not None
        try:
            raw = await self.session.call_tool(name, arguments or {})
        except Exception as e:  # 传输层故障：断管、子进程退出、协议帧非法
            return ToolResult(name=name, ok=False, text=f"传输层失败：{type(e).__name__}: {e}", raised=type(e).__name__)

        text = ""
        if raw.content:
            first = raw.content[0]
            text = getattr(first, "text", None) or str(first)
        return ToolResult(
            name=name,
            ok=not bool(raw.is_error),
            text=text,
            structured=raw.structured_content,
        )

    async def list_resources(self) -> list[dict[str, Any]]:
        assert self.session is not None
        listed = await self.session.list_resources()
        return [
            {"uri": str(r.uri), "name": r.name or "", "mime_type": r.mime_type or ""}
            for r in listed.resources
        ]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        assert self.session is not None
        got = await self.session.read_resource(uri)
        first = got.contents[0]
        return {
            "uri": str(first.uri),
            "mime_type": first.mime_type,
            "text": first.text,
        }

    async def list_prompts(self) -> list[dict[str, Any]]:
        assert self.session is not None
        listed = await self.session.list_prompts()
        return [
            {
                "name": p.name,
                "description": p.description or "",
                "arguments": [a.model_dump() for a in (p.arguments or [])],
            }
            for p in listed.prompts
        ]

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> list[dict[str, str]]:
        assert self.session is not None
        got = await self.session.get_prompt(name, arguments or {})
        out = []
        for m in got.messages:
            content = m.content
            out.append(
                {
                    "role": str(m.role),
                    "text": getattr(content, "text", "") or str(content),
                }
            )
        return out
