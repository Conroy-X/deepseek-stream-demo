"""Unit 51 · Resource —— 把只读数据挂上去。

启动：
    /home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --port 8051
    打开 http://127.0.0.1:8051/

自检：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import client as mcp_client
import server as res_server

BASE_DIR = Path(__file__).parent
STATIC_INDEX = BASE_DIR / "static" / "index.html"

app = FastAPI(title="Unit 51 · Resource", version="0.1.0")


class ReadIn(BaseModel):
    uri: str = Field(description="完整 URI，例如 doc://handbook/leave 或 doc://kb/deploy")


@app.get("/health", summary="服务端身份 + 资源/模板/工具计数")
def health() -> dict:
    catalog = mcp_client.fetch_catalog()
    return {
        "ok": True,
        "server": catalog["server"],
        "resource_count": len(catalog["resources"]),
        "template_count": len(catalog["templates"]),
        "tool_count": len(catalog["tools"]),
        "topics": res_server.list_topics(),
    }


@app.get("/api/resources", summary="固定 resource + 模板 + 可用 topic")
def api_resources() -> dict:
    catalog = mcp_client.fetch_catalog()
    return {
        "resources": catalog["resources"],
        "templates": catalog["templates"],
        "tools": catalog["tools"],
        "topics": res_server.list_topics(),
    }


@app.post("/api/read", summary="按 URI 读一个 resource（失败也返回 200，靠 ok 字段区分）")
def api_read(req: ReadIn) -> dict:
    result = mcp_client.read(req.uri)
    if not result.get("ok"):
        return {"ok": False, "uri": req.uri, "error": result["error"], "error_type": result["error_type"]}
    return {"ok": True, "uri": req.uri, **result}


@app.get("/", include_in_schema=False)
def index():
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /docs"}


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}  {detail}")

    print("Unit 51 · Resource 自检（真起 stdio 子进程）\n")

    print("  —— 文档内容本身（纯函数，不经协议）——")
    check("知识库有 3 个 topic", res_server.list_topics() == ["deploy", "onboarding", "security"],
          str(res_server.list_topics()))
    title, body = res_server.fetch_kb("Deploy")
    check("topic 大小写无关", title == "发布流程", title)
    check("deploy 正文含回滚命令", "rollback" in body)
    try:
        res_server.fetch_kb("nope")
        check("未知 topic 抛 KeyError", False)
    except KeyError as exc:
        check("未知 topic 抛 KeyError", "未知主题" in str(exc))
    check("请假制度含年假天数", "5 天" in res_server.HANDBOOK_LEAVE)
    check("变更日志是纯文本且有版本号", "0.3.0" in res_server.CHANGELOG)

    print("\n  —— 清单：resource 和 template 是两个接口 ——")
    async def probe() -> dict:
        out: dict = {}
        c = mcp_client.MCPClient()
        await c.connect()
        out["info"] = c.server_info
        out["resources"] = await c.list_resources()
        out["templates"] = await c.list_templates()
        out["tools"] = await c.list_tools()
        out["leave"] = await c.read_resource("doc://handbook/leave")
        out["changelog"] = await c.read_resource("doc://changelog")
        out["leave_again"] = await c.read_resource("doc://handbook/leave")
        out["kb"] = {t: await c.read_resource(f"doc://kb/{t}") for t in res_server.list_topics()}
        out["kb_upper"] = await c.read_resource("doc://kb/DEPLOY")
        try:
            await c.read_resource("doc://kb/nope")
            out["bad_topic"] = None
        except Exception as exc:
            out["bad_topic"] = f"{type(exc).__name__}: {exc}"
        try:
            await c.read_resource("doc://nope")
            out["bad_static"] = None
        except Exception as exc:
            out["bad_static"] = f"{type(exc).__name__}: {exc}"
        try:
            await c.read_resource("doc://kb/../etc/passwd")
            out["traversal"] = "读取成功（未被拦）"
        except Exception as exc:
            out["traversal"] = f"{type(exc).__name__}: {exc}"
        await c.close()
        return out

    facts = anyio.run(probe)
    check("服务端名 unit51-resources", facts["info"]["name"] == "unit51-resources", str(facts["info"]))
    check("能力表声明了 resources", "resources" in facts["info"]["capabilities"])
    check("固定 resource 恰好 2 个", len(facts["resources"]) == 2, str(facts["resources"]))
    check("两个 uri 就是手册与日志",
          sorted(r["uri"] for r in facts["resources"]) == ["doc://changelog", "doc://handbook/leave"])
    types = {r["uri"]: r["mime_type"] for r in facts["resources"]}
    check("手册是 text/markdown", types["doc://handbook/leave"] == "text/markdown", str(types))
    check("日志是 text/plain", types["doc://changelog"] == "text/plain")
    check("每个 resource 都有 description", all(r["description"] for r in facts["resources"]))
    check("模板恰好 1 个", len(facts["templates"]) == 1, str(facts["templates"]))
    check("模板 uri_template 带占位符",
          facts["templates"][0]["uri_template"] == "doc://kb/{topic}", str(facts["templates"][0]))
    check("模板不出现在固定 resource 列表里",
          all("{topic}" not in r["uri"] for r in facts["resources"]))
    check("这个 server 一个 tool 都没有（边界证据）", facts["tools"] == [], str(facts["tools"]))

    print("\n  —— 读取：内容、mime、回显 uri ——")
    check("读到请假制度正文", "年假" in facts["leave"]["text"], facts["leave"]["text"][:40])
    check("contents[0].mime_type 是 text/markdown", facts["leave"]["mime_type"] == "text/markdown")
    check("contents[0].uri 回显请求的 uri", facts["leave"]["uris"] == ["doc://handbook/leave"],
          str(facts["leave"]["uris"]))
    check("读纯文本 resource 的 mime 是 text/plain", facts["changelog"]["mime_type"] == "text/plain")
    check("两次读同一个 uri 内容完全一致（只读、幂等）",
          facts["leave"]["text"] == facts["leave_again"]["text"])
    check("模板展开后 uri 里是具体 topic",
          facts["kb"]["deploy"]["uris"] == ["doc://kb/deploy"], str(facts["kb"]["deploy"]["uris"]))
    check("三个 topic 内容互不相同", len({v["text"] for v in facts["kb"].values()}) == 3)
    check("模板参数大小写无关（DEPLOY 也能读到）", "发布流程" in facts["kb_upper"]["text"])
    check("每个 topic 都带 markdown 标题", all(v["text"].startswith("#") for v in facts["kb"].values()))

    print("\n  —— 失败：resource 侧没有「返回错误文本」这条通道 ——")
    check("未知 topic 让客户端抛异常", facts["bad_topic"] is not None, str(facts["bad_topic"]))
    check("异常类型是 MCPError", str(facts["bad_topic"]).startswith("MCPError"), str(facts["bad_topic"]))
    check("错误文本是模板层的通用话术",
          "Error creating resource from template" in str(facts["bad_topic"]), str(facts["bad_topic"]))
    check("底层原因（未知主题）没有传给客户端",
          "未知主题" not in str(facts["bad_topic"]), str(facts["bad_topic"]))
    check("读不存在的固定 uri 抛 Unknown resource",
          "Unknown resource" in str(facts["bad_static"]), str(facts["bad_static"]))
    check("带 ../ 的 uri 不会读到文件",
          "/etc/passwd" not in str(facts["traversal"]) or "root:" not in str(facts["traversal"]),
          str(facts["traversal"]))

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    http = TestClient(app)
    r = http.get("/health")
    h = r.json()
    check("GET /health 200", r.status_code == 200)
    check("/health 报 2 个 resource", h["resource_count"] == 2, str(h))
    check("/health 报 1 个 template", h["template_count"] == 1)
    check("/health 报 0 个 tool", h["tool_count"] == 0)
    check("/health 列出三个 topic", h["topics"] == ["deploy", "onboarding", "security"])

    r = http.get("/api/resources")
    d = r.json()
    check("/api/resources 同时给 resource 与 template", len(d["resources"]) == 2 and len(d["templates"]) == 1)

    r = http.post("/api/read", json={"uri": "doc://handbook/leave"})
    check("HTTP 读静态 resource 成功", r.json()["ok"] is True and "年假" in r.json()["text"])
    check("HTTP 读回 mime_type", r.json()["mime_type"] == "text/markdown")

    r = http.post("/api/read", json={"uri": "doc://kb/security"})
    check("HTTP 读模板 resource 成功", r.json()["ok"] is True and "安全基线" in r.json()["text"])

    r = http.post("/api/read", json={"uri": "doc://kb/nope"})
    check("HTTP 读未知 topic 返回 ok=false", r.json()["ok"] is False, str(r.json())[:120])
    check("HTTP 错误里带 error_type", r.json()["error_type"] == "MCPError", str(r.json()))

    r = http.post("/api/read", json={})
    check("缺 uri 参数 → 422", r.status_code == 422)

    r = http.get("/")
    check("GET / 返回页面", r.status_code == 200 and "Unit 51" in r.text)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
