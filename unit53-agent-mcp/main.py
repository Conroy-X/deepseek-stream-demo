"""Unit 53 · Agent + MCP 实验台（端口 8053）。

演示：Agent 当 MCP Client。把 MCP 的 input_schema 转成 OpenAI function tool，
规则模型决定调哪个工具，结果回填成 tool message，再问模型，给最终答案。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import agent as ag
import server as srv

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Unit 53 · Agent + MCP")


class AskIn(BaseModel):
    question: str


@app.on_event("startup")
def _startup() -> None:
    srv.init_db()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 53, "tools": ["list_inventory", "search_orders"]}


@app.get("/api/tools")
def api_tools() -> dict[str, Any]:
    """真起一次子进程，拿工具清单 + 转成 OpenAI function tool 形态对照。"""
    async def probe() -> dict[str, Any]:
        async with ag.MCPClient() as c:
            mcp_tools = await c.list_tools()
            openai_tools = [ag.mcp_tool_to_openai(t) for t in mcp_tools]
            return {"mcp": mcp_tools, "openai": openai_tools}
    return anyio.run(probe)


@app.post("/api/ask")
def api_ask(payload: AskIn) -> dict[str, Any]:
    """端到端：问题 → Agent loop → 答案。"""
    async def run() -> dict[str, Any]:
        async with ag.MCPClient() as c:
            tools = await c.list_tools()
            openai_tools = [ag.mcp_tool_to_openai(t) for t in tools]
            model = ag.OfflineModel(openai_tools)
            loop = ag.AgentLoop(c, model)
            return await loop.ask(payload.question)
    return anyio.run(run)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


# ── 自检 ─────────────────────────────────────────────────────────────────────

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    from fastapi.testclient import TestClient

    print("\n=== Unit 53 · Agent + MCP 自检 ===\n")

    srv.init_db()

    print("  —— schema 转换 ——")
    # 不起子进程，直接测转换函数
    fake_tool = {
        "name": "list_inventory",
        "description": "按 SKU 查库存",
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string", "description": "SKU 编号"}},
            "required": ["sku"],
        },
    }
    ot = ag.mcp_tool_to_openai(fake_tool)
    check("外层 type 是 function", ot["type"] == "function", str(ot))
    check("内层 function 壳存在", "function" in ot and "name" in ot["function"], str(ot))
    check("function.name 与原 tool 一致", ot["function"]["name"] == "list_inventory", str(ot["function"]))
    check("function.description 透传", ot["function"]["description"] == "按 SKU 查库存", str(ot["function"]))
    params = ot["function"]["parameters"]
    check("parameters.type 是 object", params["type"] == "object", str(params))
    check("parameters.properties 透传", "sku" in params["properties"], str(params))
    check("parameters.required 透传", params["required"] == ["sku"], str(params))
    check("没有丢字段", set(params.keys()) == {"type", "properties", "required"}, str(params.keys()))

    # 多参数工具
    multi = {
        "name": "search_orders",
        "description": "按客户查订单",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status": {"type": "string"},
            },
            "required": ["customer_id"],
        },
    }
    ot2 = ag.mcp_tool_to_openai(multi)
    check("多参数透传两个 properties", set(ot2["function"]["parameters"]["properties"]) == {"customer_id", "status"}, str(ot2))
    check("required 只含必填", ot2["function"]["parameters"]["required"] == ["customer_id"], str(ot2))

    # 空 schema 工具
    empty = {"name": "ping", "description": "探活", "input_schema": {}}
    ot3 = ag.mcp_tool_to_openai(empty)
    check("空 schema 兜底为 object", ot3["function"]["parameters"]["type"] == "object", str(ot3))
    check("空 schema 有 properties", "properties" in ot3["function"]["parameters"], str(ot3))

    print("  —— 工具清单（真起子进程）——")
    async def probe_tools() -> dict[str, Any]:
        async with ag.MCPClient() as c:
            tools = await c.list_tools()
            return {"tools": tools}
    facts = anyio.run(probe_tools)
    tool_names = sorted(t["name"] for t in facts["tools"])
    check("2 个工具", tool_names == ["list_inventory", "search_orders"], str(tool_names))
    list_tool = next(t for t in facts["tools"] if t["name"] == "list_inventory")
    check("list_inventory 的 sku 是必填", "sku" in list_tool["input_schema"]["required"], str(list_tool["input_schema"]))
    check("list_inventory 的 sku 是 string", list_tool["input_schema"]["properties"]["sku"]["type"] == "string", str(list_tool["input_schema"]))
    search_tool = next(t for t in facts["tools"] if t["name"] == "search_orders")
    check("search_orders 必填只 customer_id", search_tool["input_schema"]["required"] == ["customer_id"], str(search_tool["input_schema"]))
    check("search_orders 的 status 可选（不在 required）", "status" not in search_tool["input_schema"].get("required", []), str(search_tool["input_schema"]))

    openai_tools = [ag.mcp_tool_to_openai(t) for t in facts["tools"]]
    check("两个工具都能转成 OpenAI 格式", all(t["type"] == "function" for t in openai_tools), str(openai_tools))
    check("转换后 OpenAI 工具名一致", sorted(t["function"]["name"] for t in openai_tools) == ["list_inventory", "search_orders"], str(openai_tools))

    print("  —— Agent Loop（端到端）——")
    async def run_agent(q: str) -> dict[str, Any]:
        async with ag.MCPClient() as c:
            tools = await c.list_tools()
            model = ag.OfflineModel([ag.mcp_tool_to_openai(t) for t in tools])
            loop = ag.AgentLoop(c, model)
            return await loop.ask(q)

    # 问题 1：SKU 命中 → list_inventory
    r = anyio.run(run_agent, "查一下 A-100 的库存")
    check("SKU 问题拿到答案", "A-100" in r["answer"] and "库存" in r["answer"], r["answer"][:80])
    check("SKU 问题调了 list_inventory", any(t["tool_call_name"] == "list_inventory" for t in r["trace"]), str([t.get("tool_call_name") for t in r["trace"]]))
    check("调用参数 sku=A-100", any(t.get("tool_call_args") == {"sku": "A-100"} for t in r["trace"]), str([t.get("tool_call_args") for t in r["trace"]]))
    check("两轮内结束（user + assistant_call + tool + assistant_final = 4 步）", r["steps"] == 4, str(r["steps"]))
    check("模型被调了 2 次（一次 tool_call + 一次最终）", r["tool_calls"] == 2, str(r["tool_calls"]))
    check("tool_call.arguments 是字符串（agent 内部 json.loads 解出来）", any(isinstance(t.get("tool_call_args"), dict) for t in r["trace"]), str(r["trace"]))

    # 问题 2：客户号 + 状态命中 → search_orders
    r = anyio.run(run_agent, "c-200 的已发货订单有哪些？")
    check("客户问题拿到答案", "c-200" in r["answer"] and "shipped" in r["answer"], r["answer"][:80])
    check("客户问题调了 search_orders", any(t["tool_call_name"] == "search_orders" for t in r["trace"]), str([t.get("tool_call_name") for t in r["trace"]]))
    check("调 search_orders 时 customer_id=c-200", any((t.get("tool_call_args") or {}).get("customer_id") == "c-200" for t in r["trace"]), str([t.get("tool_call_args") for t in r["trace"]]))
    check("状态参数传了 shipped", any((t.get("tool_call_args") or {}).get("status") == "shipped" for t in r["trace"]), str([t.get("tool_call_args") for t in r["trace"]]))
    check("返回的 tool_result 含订单号", any("o-003" in (t.get("tool_result") or "") for t in r["trace"]), str([t.get("tool_result") for t in r["trace"]][:1]))

    # 问题 3：客户号不指定状态 → 默认 status 为空
    r = anyio.run(run_agent, "c-100 的所有订单")
    check("不带状态也能调", any(t.get("tool_call_name") == "search_orders" for t in r["trace"]), str([t.get("tool_call_name") for t in r["trace"]]))
    check("status 默认空字符串", any((t.get("tool_call_args") or {}).get("status") == "" for t in r["trace"]), str([t.get("tool_call_args") for t in r["trace"]]))

    # 问题 4：没匹配 → 模型直接给最终答案（不调工具）
    r = anyio.run(run_agent, "今天天气怎么样")
    check("无关问题不调工具", not any(t.get("tool_call_name") for t in r["trace"]), str([t.get("tool_call_name") for t in r["trace"]]))
    check("模型只被调 1 次", r["tool_calls"] == 1, str(r["tool_calls"]))
    check("最终答案说明帮不上", "帮不上" in r["answer"] or "没有" in r["answer"], r["answer"][:80])

    # 问题 5：未知 SKU → 工具返回中文提示（不抛异常）
    r = anyio.run(run_agent, "查 X-999 的库存")
    check("未知 SKU 也跑通", any(t.get("tool_call_name") == "list_inventory" for t in r["trace"]), str([t.get("tool_call_name") for t in r["trace"]]))
    check("未知 SKU 给中文提示", any("没有这个 SKU" in (t.get("tool_result") or "") for t in r["trace"]), str([t.get("tool_result") for t in r["trace"]][:1]))

    print("  —— 消息结构合法（OpenAI 格式）——")
    async def run_capture(q: str) -> list[dict[str, Any]]:
        async with ag.MCPClient() as c:
            tools = await c.list_tools()
            model = ag.OfflineModel([ag.mcp_tool_to_openai(t) for t in tools])
            loop = ag.AgentLoop(c, model)
            await loop.ask(q)
            return loop.messages
    msgs = anyio.run(run_capture, "A-100 库存")
    check("消息序列长度合理（user + assistant + tool + final）", len(msgs) == 4, str(len(msgs)))
    check("第一条是 user", msgs[0]["role"] == "user", str(msgs[0]))
    check("第二条 assistant 带 tool_calls", msgs[1]["role"] == "assistant" and "tool_calls" in msgs[1], str(msgs[1]))
    check("tool_calls[0].function.arguments 是字符串", isinstance(msgs[1]["tool_calls"][0]["function"]["arguments"], str), str(type(msgs[1]["tool_calls"][0]["function"]["arguments"])))
    check("第三条是 tool 角色", msgs[2]["role"] == "tool", str(msgs[2]))
    check("tool message 带 tool_call_id", "tool_call_id" in msgs[2], str(msgs[2]))
    check("第四条 assistant 给最终 content", msgs[3]["role"] == "assistant" and msgs[3].get("content"), str(msgs[3]))

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    tools_resp = client.get("/api/tools").json()
    check("/api/tools 返回 mcp + openai 两份", "mcp" in tools_resp and "openai" in tools_resp, str(list(tools_resp.keys())))
    check("/api/tools 的 openai 工具是 function 类型", all(t["type"] == "function" for t in tools_resp["openai"]), str(tools_resp["openai"]))
    check("/api/tools 转换后字段无损", tools_resp["openai"][0]["function"]["parameters"]["properties"] == tools_resp["mcp"][0]["input_schema"]["properties"], "字段不一致")

    r = client.post("/api/ask", json={"question": "A-100 库存"})
    check("/api/ask 端到端跑通", r.status_code == 200 and "A-100" in r.json()["answer"], r.text[:120])
    r = client.post("/api/ask", json={"question": "c-200 已发货订单"})
    check("/api/ask 客户问题跑通", r.status_code == 200 and "c-200" in r.json()["answer"], r.text[:120])

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200, str(page.status_code))
    check("页面含关键文案", "Agent" in page.text and len(page.text) > 4000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()