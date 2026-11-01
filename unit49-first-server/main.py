"""Unit 49 · 第一个 Server：终于用 SDK。

实验台跑在 8049。它自己也是一个 MCP 客户端 —— 每次请求都真的起一个 stdio 子进程、
真的握手、真的调工具。页面上看到的就是实测，不是 mock。

自检：
    python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from client import MCPClient

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Unit 49 · 第一个 Server")


class CallIn(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


class PromptIn(BaseModel):
    name: str
    arguments: dict[str, str] = {}


# ── 一次连接做完所有事（顺便证明「一条连接 = 一次握手」）────────────────────


async def snapshot() -> dict[str, Any]:
    async with MCPClient() as c:
        tools = await c.list_tools()
        resources = await c.list_resources()
        prompts = await c.list_prompts()
        return {
            "server_name": c.server_name,
            "protocol_version": c.protocol_version,
            "capabilities": c.capabilities(),
            "tools": tools,
            "resources": resources,
            "prompts": prompts,
            "handshakes": 1,
        }


# ── 接口 ─────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, Any]:
    snap = await snapshot()
    return {
        "ok": True,
        "unit": 49,
        "server": snap["server_name"],
        "protocol_version": snap["protocol_version"],
        "capabilities": snap["capabilities"],
        "counts": {
            "tools": len(snap["tools"]),
            "resources": len(snap["resources"]),
            "prompts": len(snap["prompts"]),
        },
    }


@app.get("/api/snapshot")
async def api_snapshot() -> dict[str, Any]:
    return await snapshot()


@app.post("/api/call")
async def api_call(payload: CallIn) -> dict[str, Any]:
    async with MCPClient() as c:
        result = await c.call_tool(payload.name, payload.arguments)
        return result.to_dict()


@app.get("/api/resource")
async def api_resource(uri: str) -> dict[str, Any]:
    async with MCPClient() as c:
        try:
            return await c.read_resource(uri)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"{type(e).__name__}: {e}") from e


@app.post("/api/prompt")
async def api_prompt(payload: PromptIn) -> dict[str, Any]:
    async with MCPClient() as c:
        try:
            return {"messages": await c.get_prompt(payload.name, payload.arguments)}
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"{type(e).__name__}: {e}") from e


@app.get("/api/pitfalls")
async def api_pitfalls() -> dict[str, Any]:
    """三个 2.x 的坑，现场演示一遍。"""
    async with MCPClient() as c:
        sanitized = await c.call_tool("boom")
        zero = await c.call_tool("safe_div", {"a": 1, "b": 0})
        unknown = await c.call_tool("no_such_tool")
        bad_args = await c.call_tool("add", {"a": "不是数字", "b": 1})
        return {
            "pitfalls": [
                {
                    "id": "sanitized",
                    "title": "工具抛异常 → 原因被脱敏",
                    "observed": sanitized.to_dict(),
                    "explain": "SDK 只回 `Error executing tool boom`，真实 traceback 只打到服务端 stderr。"
                    "模型看到这句话完全不知道该怎么改。",
                },
                {
                    "id": "self-caught",
                    "title": "工具自己接住异常 → 原因能传到模型",
                    "observed": zero.to_dict(),
                    "explain": "代价是 is_error=False。要么模型能读懂原因，要么协议层标记失败但模型变瞎 —— 二选一。",
                },
                {
                    "id": "unknown-tool",
                    "title": "工具不存在 → 也是 is_error，客户端不抛",
                    "observed": unknown.to_dict(),
                    "explain": "这一条我第一次测错了，写成了「客户端抛异常」。真实原因是我在服务端 print 了调试信息，"
                    "脏了 stdout 协议流 —— 观测到的是自己调试代码的副作用，不是 SDK 行为。",
                },
                {
                    "id": "bad-args",
                    "title": "参数不合法 → 反而泄露实现细节",
                    "observed": bad_args.to_dict(),
                    "explain": "文本里有 `addArguments`（SDK 自动生成的内部类名）和 pydantic 文档链接。"
                    "这类噪音直接喂给模型，它会开始模仿这些字段名 —— 参数校验得自己做前置兜底。",
                },
            ]
        }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


# ── 自检 ─────────────────────────────────────────────────────────────────────

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


async def session_checks() -> None:
    print("  —— 握手与能力声明 ——")
    async with MCPClient() as c:
        check("拿到了 server 名字", c.server_name == "hello-mcp", c.server_name)
        check("拿到了协议版本", bool(c.protocol_version), c.protocol_version)
        caps = c.capabilities()
        check("能力表声明了三类", set(caps) >= {"tools", "resources", "prompts"}, str(sorted(caps)))

        print("  —— 工具清单 ——")
        tools = await c.list_tools()
        names = sorted(t["name"] for t in tools)
        check("工具 4 个", len(tools) == 4, str(names))
        check("工具名正确", names == ["add", "boom", "greet", "safe_div"], str(names))
        add = next(t for t in tools if t["name"] == "add")
        check("add 的 schema 有 a/b", set(add["input_schema"]["properties"]) == {"a", "b"}, str(add["input_schema"]))
        check("add 的 a/b 都必填", add["input_schema"]["required"] == ["a", "b"], str(add["input_schema"].get("required")))
        check("类型注解映射成 number", add["input_schema"]["properties"]["a"]["type"] == "number", str(add["input_schema"]["properties"]["a"]))
        greet = next(t for t in tools if t["name"] == "greet")
        check("greet 的 name 是 string", greet["input_schema"]["properties"]["name"]["type"] == "string", str(greet["input_schema"]))
        check("每个工具都有描述", all(t["description"] for t in tools), "有缺描述的")

        print("  —— 正常调用 ——")
        r = await c.call_tool("greet", {"name": "rik"})
        check("greet 成功", r.ok is True, r.to_dict())
        check("greet 回显中文", r.text == "你好，rik！", r.text)
        r = await c.call_tool("add", {"a": 2, "b": 3.5})
        check("add 成功", r.ok is True, r.to_dict())
        check("add 返回 5.5", r.text.startswith("5.5"), r.text)
        check("structured_content 被包了 result", r.structured == {"result": 5.5}, str(r.structured))

        print("  —— 坑一：异常被脱敏 ——")
        r = await c.call_tool("boom")
        check("boom 标记为失败", r.ok is False, r.to_dict())
        check("boom 文本被脱敏", r.text == "Error executing tool boom", r.text)
        check("中文原因确实没传回来", "这条中文原因不会传回客户端" not in r.text, r.text)

        print("  —— 坑一的解法：自己接住 ——")
        r = await c.call_tool("safe_div", {"a": 1, "b": 0})
        check("safe_div 没被标记为失败", r.ok is True, r.to_dict())
        check("safe_div 把原因送回来了", "除数为 0" in r.text, r.text)
        r = await c.call_tool("safe_div", {"a": 9, "b": 3})
        check("safe_div 正常计算", r.text.startswith("3.0"), r.text)

        print("  —— 坑二：失败一律返回 is_error，客户端不抛异常 ——")
        r = await c.call_tool("no_such_tool")
        check("未知工具没让进程崩", r.ok is False, r.to_dict())
        check("未知工具没有抛异常（raised 为空）", r.raised is None, str(r.to_dict()))
        check("未知工具文本说明是什么错", r.text == "Unknown tool: no_such_tool", r.text)
        r = await c.call_tool("add", {"a": "不是数字", "b": 1})
        check("参数非法也是 is_error", r.ok is False, r.to_dict())
        check("参数非法也没抛异常", r.raised is None, str(r.to_dict()))
        # 关键对比：参数错误反而泄露了实现细节
        check("参数错误文本含内部类名 addArguments", "addArguments" in r.text, r.text[:120])
        check("参数错误文本含 pydantic 文档链接", "errors.pydantic.dev" in r.text, r.text[:160])
        check("参数错误仍说清了期望类型", "valid number" in r.text, r.text[:160])

        print("  —— 资源与提示词 ——")
        res = await c.list_resources()
        check("资源 1 个", len(res) == 1, str(res))
        check("资源 uri 正确", res[0]["uri"] == "doc://hello/readme", str(res[0]))
        body = await c.read_resource("doc://hello/readme")
        check("读到资源正文", "hello-mcp" in body["text"], body["text"][:40])
        check("mimeType 是 markdown", body["mime_type"] == "text/markdown", str(body["mime_type"]))

        pl = await c.list_prompts()
        check("提示词 1 个", len(pl) == 1, str(pl))
        check("提示词带参数声明", len(pl[0]["arguments"]) == 1, str(pl[0]))
        msgs = await c.get_prompt("intro", {"topic": "RAG"})
        check("prompt 返回 messages", len(msgs) == 1, str(msgs))
        check("prompt 是 user 角色", msgs[0]["role"] == "user", str(msgs[0]))
        check("参数被渲染进去", "RAG" in msgs[0]["text"], msgs[0]["text"])

    print("  —— 坑三：一条连接一次握手 ——")
    async with MCPClient() as c2:
        check("新连接拿到同一个 server 名字", c2.server_name == "hello-mcp", c2.server_name)
        # 每个 MCPClient 都起一个全新子进程，server 里的 CALLS 计数器归零。
        r = await c2.call_tool("greet", {"name": "第二个进程"})
        check("新连接独立可用", r.ok and "第二个进程" in r.text, r.to_dict())


def main() -> None:
    from fastapi.testclient import TestClient

    print("\n=== Unit 49 · 第一个 Server 自检 ===\n")

    anyio.run(session_checks)

    print("  —— HTTP 层（每请求一次真实子进程）——")
    client = TestClient(app)
    r = client.get("/health")
    check("/health 200", r.status_code == 200, str(r.status_code))
    h = r.json()
    check("health 报出 server 名", h["server"] == "hello-mcp", str(h.get("server")))
    check("health 报出三类数量", h["counts"] == {"tools": 4, "resources": 1, "prompts": 1}, str(h["counts"]))
    check("health 报出能力表", set(h["capabilities"]) >= {"tools", "resources", "prompts"}, str(h["capabilities"]))

    snap = client.get("/api/snapshot").json()
    check("snapshot 一次握手拿全", snap["handshakes"] == 1 and len(snap["tools"]) == 4, str(snap.get("counts")))
    check("snapshot 带协议版本", bool(snap["protocol_version"]), str(snap.get("protocol_version")))
    check("snapshot 工具含 input_schema", all("input_schema" in t for t in snap["tools"]), "有缺 schema 的")

    r = client.post("/api/call", json={"name": "greet", "arguments": {"name": "HTTP"}})
    check("/api/call 可用", r.status_code == 200 and "HTTP" in r.json()["text"], r.text[:120])
    r = client.post("/api/call", json={"name": "boom", "arguments": {}})
    check("/api/call 也能返回失败", r.json()["ok"] is False, r.text[:120])
    r = client.post("/api/call", json={"name": "no_such", "arguments": {}})
    check("未知工具不产生 500", r.status_code == 200 and r.json()["ok"] is False, f"{r.status_code} {r.text[:100]}")

    r = client.get("/api/resource", params={"uri": "doc://hello/readme"})
    check("/api/resource 可用", r.status_code == 200 and "hello-mcp" in r.json()["text"], r.text[:120])
    r = client.get("/api/resource", params={"uri": "doc://nope"})
    check("未知资源返回 404", r.status_code == 404, str(r.status_code))

    r = client.post("/api/prompt", json={"name": "intro", "arguments": {"topic": "MCP"}})
    check("/api/prompt 可用", r.status_code == 200 and "MCP" in r.json()["messages"][0]["text"], r.text[:120])

    pit = client.get("/api/pitfalls").json()["pitfalls"]
    check("坑演示有 4 条", len(pit) == 4, str(len(pit)))
    by_id = {p["id"]: p for p in pit}
    check("脱敏那条文本是英文模板", by_id["sanitized"]["observed"]["text"] == "Error executing tool boom", str(by_id["sanitized"]["observed"]))
    check("自兜那条 ok 为 true", by_id["self-caught"]["observed"]["ok"] is True, str(by_id["self-caught"]["observed"]))
    check("未知工具那条文本是 Unknown tool", by_id["unknown-tool"]["observed"]["text"] == "Unknown tool: no_such_tool", str(by_id["unknown-tool"]["observed"]))
    check("参数非法那条泄露了 addArguments", "addArguments" in by_id["bad-args"]["observed"]["text"], str(by_id["bad-args"]["observed"])[:120])
    check("四条坑的 observed 都不含 raised 字段", all("raised" not in p["observed"] for p in pit), str([p["id"] for p in pit]))

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200, str(page.status_code))
    check("页面含关键文案", "第一个 Server" in page.text and len(page.text) > 5000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
