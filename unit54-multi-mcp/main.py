"""Unit 54 · 多 MCP Server 实验台（端口 8054）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from multi_client import MultiMCPClient, ServerStatus, route_by_keyword

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Unit 54 · 多 MCP Server")


class AskIn(BaseModel):
    question: str


class CallIn(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 54, "servers": ["math", "text", "db"]}


async def _snapshot_async(include_bad: bool = False) -> dict[str, Any]:
    specs = [("math", "math_server.py"), ("text", "text_server.py"), ("db", "db_server.py")]
    if include_bad:
        specs.append(("broken", "no_such_server.py"))
    async with MultiMCPClient(specs) as c:
        return {
            "statuses": [s.__dict__ for s in c.statuses],
            "connected": c.connected_count,
            "failed": c.failed_count,
            "tools": c.list_tools(),
            "tools_by_ns": {ns: ts for ns, ts in c.list_tools_by_ns().items()},
            "conflicts": c.has_conflicts(),
        }


@app.get("/api/snapshot")
def api_snapshot() -> dict[str, Any]:
    return anyio.run(_snapshot_async)


@app.get("/api/degraded")
def api_degraded() -> dict[str, Any]:
    """故意让一个 server 路径错误，证明剩下仍可用。"""
    return anyio.run(_snapshot_async, True)


@app.post("/api/call")
def api_call(payload: CallIn) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with MultiMCPClient() as c:
            r = await c.call_tool(payload.name, payload.arguments)
            return r.to_dict()
    return anyio.run(run)


@app.post("/api/ask")
def api_ask(payload: AskIn) -> dict[str, Any]:
    """按问题关键词路由到 namespace，调对应工具。"""
    async def run() -> dict[str, Any]:
        async with MultiMCPClient() as c:
            by_ns = c.list_tools_by_ns()
            ns = route_by_keyword(payload.question, by_ns)
            if ns is None:
                return {"answer": "找不到对应的 server", "routed_to": None}
            # 简化：每个 ns 取第一个工具做演示
            first_tool = by_ns[ns][0]
            r = await c.call_tool(first_tool["name"], {})
            return {
                "answer": f"路由到 {ns}，调用 {first_tool['name']}：{r.text}",
                "routed_to": ns,
                "tool": first_tool["name"],
                "raw_result": r.to_dict(),
            }
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

    print("\n=== Unit 54 · 多 MCP Server 自检 ===\n")

    print("  —— 三个 server 全连 ——")
    async def probe_all() -> dict[str, Any]:
        async with MultiMCPClient() as c:
            return {
                "statuses": [s.__dict__ for s in c.statuses],
                "tools": c.list_tools(),
                "by_ns": c.list_tools_by_ns(),
                "conflicts": c.has_conflicts(),
            }
    facts = anyio.run(probe_all)
    check("3 个 server 全连上", sum(1 for s in facts["statuses"] if s["connected"]) == 3, str(facts["statuses"]))
    check("没有失败的 server", sum(1 for s in facts["statuses"] if not s["connected"]) == 0, str(facts["statuses"]))
    check("3 个 namespace", sorted(s["name"] for s in facts["statuses"]) == ["db", "math", "text"], str(facts["statuses"]))
    # 工具数：math.add, math.square, text.upper, text.reverse, db.add, db.query = 6
    check("聚合工具数 = 6", len(facts["tools"]) == 6, str(len(facts["tools"])))
    tool_names = sorted(t["name"] for t in facts["tools"])
    check("工具名带前缀", all("." in n for n in tool_names), str(tool_names))
    check("前缀正确", tool_names == ["db.add", "db.query", "math.add", "math.square", "text.reverse", "text.upper"], str(tool_names))
    check("每个 namespace 2 个工具", all(len(ts) == 2 for ts in facts["by_ns"].values()), str({k: len(v) for k, v in facts["by_ns"].items()}))

    print("  —— 工具名冲突 ——")
    conflicts = facts["conflicts"]
    check("add 在两个 namespace 出现", "add" in conflicts, str(conflicts))
    check("add 的冲突是 math/db", conflicts.get("add") == ["db", "math"], str(conflicts.get("add")))
    check("冲突靠前缀隔离（db.add != math.add）", "db.add" in tool_names and "math.add" in tool_names, str(tool_names))
    check("其他工具无冲突", set(conflicts) == {"add"}, str(conflicts))

    print("  —— 调用带前缀的工具 ——")
    async def probe_call() -> dict[str, Any]:
        async with MultiMCPClient() as c:
            r1 = await c.call_tool("math.add", {"a": 2, "b": 3})
            r2 = await c.call_tool("math.square", {"x": 4})
            r3 = await c.call_tool("text.upper", {"text": "hello"})
            r4 = await c.call_tool("text.reverse", {"text": "abc"})
            r5 = await c.call_tool("db.query", {"sku": "A-100"})
            r6 = await c.call_tool("db.add", {"sku": "A-100", "qty": 5})
            r7 = await c.call_tool("db.add", {"sku": "A-100", "qty": 5})  # 第二次加
            unknown = await c.call_tool("nope.x", {})
            return {"r1": r1.to_dict(), "r2": r2.to_dict(), "r3": r3.to_dict(),
                    "r4": r4.to_dict(), "r5": r5.to_dict(), "r6": r6.to_dict(),
                    "r7": r7.to_dict(), "unknown": unknown.to_dict()}
    calls = anyio.run(probe_call)
    check("math.add(2,3)=5", calls["r1"]["ok"] and "5" in calls["r1"]["text"], str(calls["r1"]))
    check("math.square(4)=16", calls["r2"]["ok"] and "16" in calls["r2"]["text"], str(calls["r2"]))
    check("text.upper(hello)=HELLO", calls["r3"]["ok"] and calls["r3"]["text"] == "HELLO", str(calls["r3"]))
    check("text.reverse(abc)=cba", calls["r4"]["ok"] and calls["r4"]["text"] == "cba", str(calls["r4"]))
    check("db.query(A-100) 库存", calls["r5"]["ok"] and "12" in calls["r5"]["text"], str(calls["r5"]))
    check("db.add 第一次加 5", calls["r6"]["ok"] and "17" in calls["r6"]["text"], str(calls["r6"]))
    check("db.add 第二次再加 5（22）", calls["r7"]["ok"] and "22" in calls["r7"]["text"], str(calls["r7"]))
    check("db.add 与 math.add 不串", "17" not in calls["r1"]["text"] and "5" not in calls["r6"]["text"], "状态串了")
    check("未知工具被接住", calls["unknown"]["ok"] is False, str(calls["unknown"]))

    print("  —— 部分失败降级 ——")
    async def probe_degraded() -> dict[str, Any]:
        specs = [("math", "math_server.py"), ("text", "text_server.py"), ("db", "db_server.py"), ("broken", "no_such_server.py")]
        async with MultiMCPClient(specs) as c:
            # broken 应当连接失败，但其他 3 个仍可用
            r = await c.call_tool("math.add", {"a": 1, "b": 1})
            return {
                "statuses": [s.__dict__ for s in c.statuses],
                "math_call": r.to_dict(),
            }
    deg = anyio.run(probe_degraded)
    broken = next((s for s in deg["statuses"] if s["name"] == "broken"), None)
    check("broken server 标记为未连接", broken is not None and not broken["connected"], str(broken))
    check("broken 的错误被记下来", broken and broken["error"], str(broken))
    check("其他 3 个仍连上", sum(1 for s in deg["statuses"] if s["connected"]) == 3, str(deg["statuses"]))
    check("降级后仍能调 math.add", deg["math_call"]["ok"] and "2" in deg["math_call"]["text"], str(deg["math_call"]))

    print("  —— 关键词路由 ——")
    from multi_client import route_by_keyword
    by_ns = facts["by_ns"]
    check("数学问题路由到 math", route_by_keyword("两数相加", by_ns) == "math", str(route_by_keyword("两数相加", by_ns)))
    check("文本问题路由到 text", route_by_keyword("转大写", by_ns) == "text", str(route_by_keyword("转大写", by_ns)))
    check("库存问题路由到 db", route_by_keyword("查库存", by_ns) == "db", str(route_by_keyword("查库存", by_ns)))
    check("无关问题不路由", route_by_keyword("今天天气", by_ns) is None, str(route_by_keyword("今天天气", by_ns)))

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    snap = client.get("/api/snapshot").json()
    check("/api/snapshot 3 个 server 全连", snap["connected"] == 3 and snap["failed"] == 0, str(snap))
    check("/api/snapshot 6 个工具", len(snap["tools"]) == 6, str(len(snap["tools"])))
    check("/api/snapshot 有冲突报告", "add" in snap["conflicts"], str(snap["conflicts"]))

    deg_resp = client.get("/api/degraded").json()
    check("/api/degraded 1 个失败", deg_resp["failed"] == 1, str(deg_resp))
    check("/api/degraded 3 个仍连", deg_resp["connected"] == 3, str(deg_resp))

    r = client.post("/api/call", json={"name": "math.add", "arguments": {"a": 10, "b": 20}})
    check("/api/call math.add(10,20)=30", r.json()["ok"] and "30" in r.json()["text"], r.text[:120])
    r = client.post("/api/call", json={"name": "text.upper", "arguments": {"text": "world"}})
    check("/api/call text.upper", r.json()["ok"] and r.json()["text"] == "WORLD", r.text[:120])

    r = client.post("/api/ask", json={"question": "两数相加"})
    check("/api/ask 路由到 math", r.json()["routed_to"] == "math", str(r.json()))
    r = client.post("/api/ask", json={"question": "转大写"})
    check("/api/ask 路由到 text", r.json()["routed_to"] == "text", str(r.json()))
    r = client.post("/api/ask", json={"question": "查库存"})
    check("/api/ask 路由到 db", r.json()["routed_to"] == "db", str(r.json()))
    r = client.post("/api/ask", json={"question": "今天天气"})
    check("/api/ask 无关问题不路由", r.json()["routed_to"] is None, str(r.json()))

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200, str(page.status_code))
    check("页面含关键文案", "多 MCP" in page.text and len(page.text) > 4000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()