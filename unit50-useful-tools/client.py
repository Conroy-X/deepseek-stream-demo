"""Unit 50 · MCP 客户端封装（stdio）。

main.py 里的 HTTP 接口是同步的，而 MCP 客户端是异步的，所以这里的策略是：
**每个请求起一次子进程**，跑完就关。代价是每次约 0.7 秒，好处是没有跨事件循环的
共享状态 —— FastAPI 的同步路由跑在线程池里，任何跨线程复用的 session 都会踩坑。

自检里相反：一次连接跑十几个调用，省掉十几次启动开销。
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 自检与实验台共用同一个解释器：必须绝对路径，否则拿到的是系统 python。
PYTHON = "/home/rik/.cache/aj/runner/bin/python"
BASE_DIR = Path(__file__).parent
SERVER_PATH = BASE_DIR / "server.py"


def parse_tool(tool: Any) -> dict:
    """把 SDK 的 Tool 模型摊成纯 dict —— 前端只认 JSON。"""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.input_schema or {"type": "object", "properties": {}},
    }


def flatten_result(result: Any) -> dict:
    """CallToolResult -> 前端能直接渲染的形状。

    `content` 可能有多块，这里全部取 text；非 text 块（图片等）只记类型。
    """
    texts: list[str] = []
    kinds: list[str] = []
    for block in result.content or []:
        kinds.append(getattr(block, "type", "unknown"))
        if getattr(block, "type", "") == "text":
            texts.append(block.text)
    return {
        "is_error": bool(result.is_error),
        "text": "\n".join(texts),
        "blocks": kinds,
        "structured": getattr(result, "structured_content", None),
    }


class MCPClient:
    """一个连接 = 一个 stdio 子进程 + 一次握手。用完必须 close()。"""

    def __init__(self, server_path: Path | str = SERVER_PATH, command: str = PYTHON) -> None:
        self.server_path = Path(server_path)
        self.command = command
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self.server_info: dict = {}

    async def connect(self) -> dict:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        params = StdioServerParameters(command=self.command, args=[str(self.server_path)])
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        init = await self._session.initialize()
        self.server_info = {
            "name": init.server_info.name,
            "version": init.server_info.version,
            "protocol_version": init.protocol_version,
            "capabilities": sorted(
                k for k, v in init.capabilities.model_dump(exclude_none=True).items() if v is not None
            ),
        }
        return self.server_info

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("还没 connect()")
        return self._session

    async def list_tools(self) -> list[dict]:
        result = await self.session.list_tools()
        return [parse_tool(t) for t in result.tools]

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        result = await self.session.call_tool(name, arguments or {})
        return flatten_result(result)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None


async def _with_client(fn):
    client = MCPClient()
    try:
        await client.connect()
        return await fn(client)
    finally:
        await client.close()


def fetch_catalog() -> dict:
    """一次性取握手信息 + 工具清单（给 /health 和 /api/tools 用）。"""

    async def job(client: MCPClient) -> dict:
        tools = await client.list_tools()
        return {"server": client.server_info, "tools": tools}

    return anyio.run(_with_client, job)


def invoke(name: str, arguments: dict | None = None) -> dict:
    """一次性调用一个工具。异常一律转成统一形状，HTTP 层只判断 ok。"""

    async def job(client: MCPClient) -> dict:
        return await client.call_tool(name, arguments)

    try:
        return anyio.run(_with_client, job)
    except Exception as exc:  # MCPError / 子进程起不来 / 握手失败
        return {
            "is_error": True,
            "text": f"客户端异常：{type(exc).__name__}: {exc}",
            "blocks": [],
            "structured": None,
        }
