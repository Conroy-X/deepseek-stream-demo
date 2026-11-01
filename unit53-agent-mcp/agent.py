"""Unit 53 · MCP 客户端 + schema 转换 + Agent Loop。

核心转换：

  MCP tool                   →  OpenAI function tool
  {                          →  {
    "name": "list_inventory",     "type": "function",
    "description": "...",          "function": {
    "input_schema": {                  "name": "list_inventory",
      "type": "object",                "description": "...",
      "properties": {...},              "parameters": {
      "required": [...]                    "type": "object",
    }                                      "properties": {...},
  }                                        "required": [...]
                                        }
                                    }
                                 }
                              }

两套协议的 `properties` 和 `required` 字段完全一致 —— 这就是 Agent 不用改就能接 MCP 的关键。
"""

from __future__ import annotations

import json
import re
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PATH = Path(__file__).resolve().parent / "server.py"


# ── MCP 客户端 ──────────────────────────────────────────────────────────────


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
        if self._stack:
            await self._stack.aclose()
        self._stack = None
        self.session = None

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


# ── MCP tool → OpenAI function tool ────────────────────────────────────────


def mcp_tool_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    """把 MCP 的 tool dict 转成 OpenAI function-calling 的 tools 数组元素。

    两套协议 properties/required 字段完全一致，只在外面套一层 function 壳。
    """
    schema = tool.get("input_schema") or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": {
                "type": schema.get("type", "object"),
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        },
    }


# ── 离线规则模型（OpenAI 消息格式输出）──────────────────────────────────────


# 规则：把问题里出现的 SKU / 客户号抠出来，决定调哪个工具
SKU_RE = re.compile(r"\b([A-Z])-(\d{3})\b")
CUSTOMER_RE = re.compile(r"\bc-(\d{3})\b")
STATUS_KW = {
    "已发货": "shipped",
    "待发货": "pending",
    "已取消": "cancelled",
}


class OfflineModel:
    """规则模型。输出标准 OpenAI 消息格式：要么带 tool_calls，要么给最终 content。

    关键：`tool_calls[].function.arguments` 是 **字符串**，不是 dict —— 这是真实 OpenAI API 的行为。
    """

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self.tools = {t["function"]["name"]: t for t in tools}
        self.calls = 0

    def respond(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1

        # 关键：如果上一条是 tool message，意味着工具结果回来了 ——
        # 模型必须基于结果给最终答案，**不能再发 tool_calls**，否则死循环。
        last_role = messages[-1]["role"] if messages else ""
        if last_role == "tool":
            tool_msg = messages[-1]
            return {
                "role": "assistant",
                "content": f"根据查询结果：{tool_msg['content']}",
            }

        last_user = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user = m["content"]
                break

        # SKU 命中 → list_inventory
        m = SKU_RE.search(last_user)
        if m:
            sku = f"{m.group(1)}-{m.group(2)}"
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call_{self.calls}",
                    "type": "function",
                    "function": {
                        "name": "list_inventory",
                        # arguments 必须是 JSON 字符串，不能是 dict
                        "arguments": json.dumps({"sku": sku}, ensure_ascii=False),
                    },
                }],
            }

        # 客户号命中 → search_orders
        m = CUSTOMER_RE.search(last_user)
        if m:
            customer_id = f"c-{m.group(1)}"
            status = ""
            for kw, code in STATUS_KW.items():
                if kw in last_user:
                    status = code
                    break
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call_{self.calls}",
                    "type": "function",
                    "function": {
                        "name": "search_orders",
                        "arguments": json.dumps(
                            {"customer_id": customer_id, "status": status},
                            ensure_ascii=False,
                        ),
                    },
                }],
            }

        # 没匹配 → 给最终答案
        return {
            "role": "assistant",
            "content": "我帮不上忙 —— 你的问题里没有 SKU 或客户编号。",
        }


# ── Agent Loop ─────────────────────────────────────────────────────────────


@dataclass
class AgentStep:
    role: str
    content: str | None
    tool_call_name: str | None
    tool_call_args: dict[str, Any] | None
    tool_result: str | None


class AgentLoop:
    """两轮内拿到答案的 Agent。结构：问 → tool_calls → 回填 tool_result → 再问 → 最终答案。"""

    def __init__(self, client: MCPClient, model: OfflineModel) -> None:
        self.client = client
        self.model = model
        self.messages: list[dict[str, Any]] = []
        self.trace: list[AgentStep] = []

    async def ask(self, question: str) -> dict[str, Any]:
        self.messages.append({"role": "user", "content": question})
        self.trace.append(AgentStep("user", question, None, None, None))

        for step in range(5):
            response = self.model.respond(self.messages)
            self.messages.append(response)

            if not response.get("tool_calls"):
                # 模型给最终答案
                self.trace.append(AgentStep("assistant", response.get("content"), None, None, None))
                return {
                    "answer": response["content"],
                    "steps": len(self.trace),
                    "tool_calls": self.model.calls,
                    "trace": [t.__dict__ for t in self.trace],
                }

            # 处理所有 tool_calls（一般只有一个）
            for call in response["tool_calls"]:
                name = call["function"]["name"]
                args = json.loads(call["function"]["arguments"])  # 字符串 → dict
                self.trace.append(AgentStep("assistant", None, name, args, None))

                result = await self.client.call_tool(name, args)
                # 回填成 OpenAI 的 tool message
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": result.text,
                })
                self.trace.append(AgentStep("tool", result.text, name, args, result.text))

        return {
            "answer": "达到最大轮次仍未结束。",
            "steps": len(self.trace),
            "tool_calls": self.model.calls,
            "trace": [t.__dict__ for t in self.trace],
        }