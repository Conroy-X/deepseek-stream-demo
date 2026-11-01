"""stdio 传输 + ClientSession 的薄封装。

只有三件事值得记住：
1. `command` 必须是**绝对路径**（`sys.executable`），否则非交互 shell 里会拿到系统 python；
2. 一次 stdio 往返约 0.7 秒（起进程 + 握手），所以 `call_many()` 一次会话跑多条，
   别一条 call 起一个进程；
3. 同步代码里跑异步用 `anyio.run`，**不要** `asyncio.run`（SDK 内部是 anyio task group）。

本模块对外只暴露同步函数，调用方（main.py / agent.py）不需要碰 async。
"""

from __future__ import annotations

import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE_DIR = Path(__file__).resolve().parent
SERVER_PATH = BASE_DIR / "server.py"


def server_params() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=[str(SERVER_PATH)], cwd=str(BASE_DIR))


@asynccontextmanager
async def session():
    """一次完整的 stdio 会话：起子进程 → 握手 → 交给你用 → 关掉。"""
    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            yield s, init


def text_of(result) -> str:
    """MCP 工具结果的第一段文本。协议里结果永远是 content 数组，不是裸值。"""
    if not result.content:
        return ""
    return getattr(result.content[0], "text", "") or ""


async def _collect(calls: list[tuple[str, dict]]) -> dict:
    async with session() as (s, init):
        listed = await s.list_tools()
        out = []
        for name, args in calls:
            started = time.perf_counter()
            res = await s.call_tool(name, args)
            out.append({
                "name": name,
                "args": args,
                "is_error": bool(res.is_error),
                "text": text_of(res),
                "ms": round((time.perf_counter() - started) * 1000, 1),
            })
        return {
            "server": init.server_info.name,
            "protocol": init.protocol_version,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in listed.tools
            ],
            "results": out,
        }


def call_many(calls: list[tuple[str, dict]]) -> dict:
    """一条连接里依次调多个工具。自检和 Agent 主循环都走这里。"""
    return anyio.run(lambda: _collect(calls))


def call_tool(name: str, args: dict | None = None) -> dict:
    """单次调用。HTTP 接口用这个 —— 一次请求一次会话，0.7 秒换来完全隔离。"""
    payload = call_many([(name, args or {})])
    first = payload["results"][0]
    first["server"] = payload["server"]
    first["protocol"] = payload["protocol"]
    return first


def handshake() -> dict:
    """只握手不干活，用来证明「连接是通的」。"""
    payload = call_many([])
    return {
        "server": payload["server"],
        "protocol": payload["protocol"],
        "tools": payload["tools"],
    }


def read_resource(uri: str) -> str:
    async def _run():
        async with session() as (s, _init):
            res = await s.read_resource(uri)
            return res.contents[0].text

    return anyio.run(_run)


def list_resources() -> list[str]:
    async def _run():
        async with session() as (s, _init):
            res = await s.list_resources()
            return [str(r.uri) for r in res.resources]

    return anyio.run(_run)
