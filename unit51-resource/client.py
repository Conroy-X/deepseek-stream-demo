"""Unit 51 · MCP 客户端封装（stdio）—— 只读资源，不谈工具。

和 Unit 50 的 client 相比，这里多了两件事：
  1. `list_resources` 与 `list_resource_templates` 是**两个接口**，返回的模型也不一样；
  2. `read_resource` 失败时客户端**抛异常**（不是 is_error），这一点和 call_tool 相反。
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PYTHON = "/home/rik/.cache/aj/runner/bin/python"
BASE_DIR = Path(__file__).parent
SERVER_PATH = BASE_DIR / "server.py"


def parse_resource(item: Any) -> dict:
    return {
        "uri": str(item.uri),
        "name": item.name or "",
        "description": item.description or "",
        "mime_type": item.mime_type or "",
    }


def parse_template(item: Any) -> dict:
    return {
        "uri_template": str(item.uri_template),
        "name": item.name or "",
        "description": item.description or "",
        "mime_type": item.mime_type or "",
    }


def flatten_contents(result: Any) -> dict:
    """ReadResourceResult -> dict。文本与二进制分开，前者进 text，后者只报字节数。"""
    texts: list[str] = []
    blobs: list[int] = []
    mimes: list[str] = []
    uris: list[str] = []
    for block in result.contents or []:
        uris.append(str(block.uri))
        mimes.append(block.mime_type or "")
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)
        else:
            blobs.append(len(getattr(block, "blob", b"") or b""))
    return {
        "uris": uris,
        "mime_types": mimes,
        "mime_type": mimes[0] if mimes else "",
        "text": "\n".join(texts),
        "blob_bytes": blobs,
    }


class MCPClient:
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
            "capabilities": sorted(init.capabilities.model_dump(exclude_none=True)),
        }
        return self.server_info

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("还没 connect()")
        return self._session

    async def list_tools(self) -> list[dict]:
        result = await self.session.list_tools()
        return [{"name": t.name, "description": t.description or ""} for t in result.tools]

    async def list_resources(self) -> list[dict]:
        result = await self.session.list_resources()
        return [parse_resource(r) for r in result.resources]

    async def list_templates(self) -> list[dict]:
        result = await self.session.list_resource_templates()
        return [parse_template(t) for t in result.resource_templates]

    async def read_resource(self, uri: str) -> dict:
        """读一个 resource。失败**不**吞异常，交给调用方决定怎么呈现。"""
        result = await self.session.read_resource(uri)
        return flatten_contents(result)

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
    async def job(client: MCPClient) -> dict:
        return {
            "server": client.server_info,
            "resources": await client.list_resources(),
            "templates": await client.list_templates(),
            "tools": await client.list_tools(),
        }

    return anyio.run(_with_client, job)


def read(uri: str) -> dict:
    """一次性读一个 URI。异常转成 {ok: False, error: ...}，让页面能渲染。"""

    async def job(client: MCPClient) -> dict:
        return await client.read_resource(uri)

    try:
        return {"ok": True, **anyio.run(_with_client, job)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "error_type": type(exc).__name__}
