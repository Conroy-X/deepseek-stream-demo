"""Unit 83 · MCP 集成：把 MCP 工具挂进 Research Agent。

计划主题：Unit 46–59 学了 MCP 协议，本单元做「集成」——把 MCP server 的工具
真正挂进 Research Agent，让 Agent 能发现并调用外部工具来完成研究任务。

三块：
  1. MCP Server：暴露 search / fetch 两个工具（带 schema）
  2. MCP Client：连接、发现工具、调用（含参数校验与错误处理）
  3. Research Agent：用 MCP 工具做「检索 → 抓取 → 汇总」，工具调用全程留痕

启动：uv run uvicorn main:app --port 8083
自检：uv run python main.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from fastapi import FastAPI

# ---------------------------------------------------------------------------
# MCP Server：工具注册 + schema + 调用
# ---------------------------------------------------------------------------
@dataclass
class McpTool:
    name: str
    description: str
    schema: dict          # 参数 schema（简化版 JSON Schema）
    fn: Callable[..., str]


@dataclass
class McpServer:
    tools: dict[str, McpTool] = field(default_factory=dict)

    def register(self, tool: McpTool) -> None:
        self.tools[tool.name] = tool

    def list_tools(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.schema}
            for t in self.tools.values()
        ]

    def call(self, name: str, args: dict) -> dict:
        if name not in self.tools:
            return {"ok": False, "error": f"未知工具 {name}"}
        tool = self.tools[name]
        # 参数校验：required 字段齐全
        required = tool.schema.get("required", [])
        missing = [r for r in required if r not in args]
        if missing:
            return {"ok": False, "error": f"缺少必填参数 {missing}"}
        try:
            return {"ok": True, "result": tool.fn(**args)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# MCP Client：发现 + 调用 + 留痕
# ---------------------------------------------------------------------------
@dataclass
class McpClient:
    server: McpServer
    discovered: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)

    def connect(self) -> None:
        self.discovered = self.server.list_tools()

    def has_tool(self, name: str) -> bool:
        return any(t["name"] == name for t in self.discovered)

    def invoke(self, name: str, args: dict) -> dict:
        if not self.has_tool(name):
            self.trace.append({"tool": name, "args": args, "ok": False, "error": "未发现该工具"})
            return {"ok": False, "error": "未发现该工具"}
        result = self.server.call(name, args)
        self.trace.append({"tool": name, "args": args, "ok": result["ok"]})
        return result


# ---------------------------------------------------------------------------
# 演示用的 MCP Server
# ---------------------------------------------------------------------------
def _search(q: str, limit: int = 3) -> str:
    """假检索：返回固定格式的结果列表。"""
    return "\n".join(f"- 结果{i}：关于「{q}」的资料{i}" for i in range(1, min(limit, 3) + 1))


def _fetch(url: str) -> str:
    """假抓取：返回页面正文。"""
    if not url.startswith("http"):
        raise ValueError("url 必须以 http 开头")
    return f"页面正文（来自 {url}）：这里是清洗后的纯文本内容。"


def build_server() -> McpServer:
    srv = McpServer()
    srv.register(McpTool(
        name="search",
        description="搜索资料，返回结果列表",
        schema={"type": "object", "properties": {"q": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["q"]},
        fn=_search,
    ))
    srv.register(McpTool(
        name="fetch",
        description="抓取网页正文",
        schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        fn=_fetch,
    ))
    return srv


# ---------------------------------------------------------------------------
# Research Agent：用 MCP 工具完成研究
# ---------------------------------------------------------------------------
@dataclass
class ResearchAgent:
    client: McpClient
    max_steps: int = 6

    def research(self, question: str) -> dict:
        steps = []
        # 1. 检索
        r1 = self.client.invoke("search", {"q": question, "limit": 2})
        steps.append({"step": "search", "ok": r1["ok"]})
        if not r1["ok"]:
            return {"ok": False, "error": r1["error"], "steps": steps}
        snippets = r1["result"]

        # 2. 抓取（从检索结果里取一个 url —— 演示用固定）
        r2 = self.client.invoke("fetch", {"url": f"https://example.com/search?q={question}"})
        steps.append({"step": "fetch", "ok": r2["ok"]})
        page = r2["result"] if r2["ok"] else ""

        # 3. 汇总
        report = f"# 研究报告：{question}\n\n## 结论\n\n基于 MCP 工具检索与抓取。\n\n## 证据\n\n{snippets}\n\n## 页面摘要\n\n{page}\n"
        return {"ok": True, "report": report, "steps": steps, "tools_used": len(self.client.trace)}


app = FastAPI(title="Unit 83 · MCP 集成", version="0.1.0")


@app.get("/tools", summary="MCP 工具发现（tools/list）")
async def tools() -> dict:
    srv = build_server()
    return {"tools": srv.list_tools()}


@app.get("/research", summary="Research Agent 用 MCP 工具做研究")
async def research(q: str = "年假政策") -> dict:
    srv = build_server()
    cli = McpClient(srv)
    cli.connect()
    agent = ResearchAgent(cli)
    out = agent.research(q)
    out["trace"] = cli.trace
    return out


@app.get("/", include_in_schema=False)
async def index() -> dict:
    return {"hint": "/tools 看工具发现；/research?q=年假政策 看 Agent 调用 MCP"}


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    from fastapi.testclient import TestClient

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 83 · MCP 集成 自检\n")
    srv = build_server()

    # ---- tools/list ----
    tl = srv.list_tools()
    check("发现 2 个工具", len(tl) == 2, str(len(tl)))
    check("工具带 schema", all("inputSchema" in t for t in tl))
    check("schema 有 required", all("required" in t["inputSchema"] for t in tl))

    # ---- tools/call ----
    r = srv.call("search", {"q": "年假"})
    check("search 调用成功", r["ok"] and "年假" in r["result"])
    r = srv.call("fetch", {"url": "https://example.com"})
    check("fetch 调用成功", r["ok"] and "页面正文" in r["result"])
    r = srv.call("fetch", {"url": "not-a-url"})
    check("fetch 非法 url 返回错误", r["ok"] is False, str(r))
    r = srv.call("search", {})
    check("缺必填参数报错", r["ok"] is False and "缺少" in r["error"], str(r))
    r = srv.call("nope", {})
    check("未知工具报错", r["ok"] is False)

    # ---- client ----
    cli = McpClient(srv)
    check("未连接时无工具", cli.has_tool("search") is False)
    cli.connect()
    check("连接后能发现", cli.has_tool("search") is True)
    r = cli.invoke("search", {"q": "x"})
    check("client 调用成功", r["ok"])
    check("client 留痕", len(cli.trace) == 1)
    r = cli.invoke("unknown", {})
    check("client 拒绝未发现工具", r["ok"] is False)
    check("失败也留痕", len(cli.trace) == 2)

    # ---- Research Agent ----
    cli2 = McpClient(srv)
    cli2.connect()
    agent = ResearchAgent(cli2)
    out = agent.research("年假政策")
    check("Agent 研究成功", out["ok"] is True)
    check("Agent 用了 2 个工具", out["tools_used"] == 2, str(out["tools_used"]))
    check("报告有结论段", "## 结论" in out["report"])
    check("报告有证据段", "## 证据" in out["report"])
    check("步骤记录完整", len(out["steps"]) == 2)

    # ---- HTTP ----
    c = TestClient(app)
    r = c.get("/tools")
    check("HTTP /tools 200", r.status_code == 200)
    check("HTTP 返回 2 工具", len(r.json()["tools"]) == 2)
    r = c.get("/research", params={"q": "年假政策"})
    check("HTTP /research 200", r.status_code == 200)
    body = r.json()
    check("HTTP 研究成功", body["ok"] is True)
    check("HTTP 带 trace", len(body["trace"]) == 2)
    check("HTTP trace 含工具名", body["trace"][0]["tool"] == "search")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
