"""Unit 41 · 搜索工具 —— FastAPI 实验台 + 自检。

启动：
    /home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8041

自检（离线）：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from search import (
    DEFAULT_MAX_RESULTS,
    FIXTURES,
    HARD_MAX_RESULTS,
    SEARCH_SCHEMA,
    list_fixtures,
    search,
    search_tool_spec,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 41 · 搜索工具", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, ge=0, le=HARD_MAX_RESULTS)


@app.get("/health", summary="后端存活 + fixture 概览")
def health() -> dict:
    return {
        "ok": True,
        "unit": "unit41-search-tool",
        "fixture_count": len(FIXTURES),
        "topics": sorted({r["topic"] for r in FIXTURES}),
        "default_max_results": DEFAULT_MAX_RESULTS,
        "hard_max_results": HARD_MAX_RESULTS,
    }


@app.get("/fixtures", summary="列全部 fixture（让前端看到工具背后到底有什么）")
def fixtures() -> dict:
    return {"count": len(FIXTURES), "items": list_fixtures()}


@app.get("/tools", summary="OpenAI function-calling 格式的工具声明")
def tools() -> dict:
    return {"tools": [search_tool_spec()]}


@app.get("/schema", summary="裸 schema —— 看参数长什么样")
def schema() -> dict:
    return SEARCH_SCHEMA


@app.post("/search", summary="搜一下")
def do_search(req: SearchIn) -> dict:
    results = search(req.query, req.max_results)
    return {
        "query": req.query,
        "max_results": req.max_results,
        "count": len(results),
        "results": results,
    }


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
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

    print("Unit 41 · 搜索工具 自检（离线）\n")

    print("  —— fixture 全覆盖 ——")
    check("fixture 正好 5 条", len(FIXTURES) == 5, str(len(FIXTURES)))
    topics = sorted({r["topic"] for r in FIXTURES})
    check("fixture 覆盖 3 个主题", len(topics) == 3, str(topics))
    check("3 个主题分别为 MCP 协议 / RAG 优化 / LangGraph",
          topics == ["LangGraph", "MCP 协议", "RAG 优化"], str(topics))
    check("每条 fixture 都有 title/url/snippet",
          all(r.get("title") and r.get("url") and r.get("snippet") for r in FIXTURES))
    check("每条 fixture 有 id（页面展示用）",
          all(r.get("id") for r in FIXTURES))
    mcp_count = sum(1 for r in FIXTURES if r["topic"] == "MCP 协议")
    rag_count = sum(1 for r in FIXTURES if r["topic"] == "RAG 优化")
    lg_count = sum(1 for r in FIXTURES if r["topic"] == "LangGraph")
    check("MCP 协议 2 条 / RAG 优化 2 条 / LangGraph 1 条",
          mcp_count == 2 and rag_count == 2 and lg_count == 1,
          f"{mcp_count}/{rag_count}/{lg_count}")

    print("\n  —— 按关键词命中 ——")
    r = search("MCP 协议")
    check("查 MCP 协议：命中 2 条", len(r) == 2, str(len(r)))
    check("结果只暴露 title/url/snippet 三字段",
          all(set(x.keys()) == {"title", "url", "snippet"} for x in r),
          str(r[0].keys()) if r else "empty")
    check("结果里不带 topic / id（别把内部字段漏出去）",
          all("topic" not in x and "id" not in x for x in r))

    r = search("RAG")
    check("查 RAG：命中 2 条", len(r) == 2, str(len(r)))
    check("RAG 结果都是 RAG 优化主题",
          all("RAG" in x["title"] or "RAG" in x["snippet"] for x in r))

    r = search("LangGraph")
    check("查 LangGraph：命中 1 条", len(r) == 1, str(len(r)))

    r = search("chunk reranker")
    check("多词 query：命中 chunk/reranker 那条", len(r) == 1 and "reranker" in r[0]["snippet"].lower(), str(r))

    r = search("isError error 帧")
    check("多词 query：命中 isError 那条", len(r) == 1 and "isError" in r[0]["snippet"], str(r))

    r = search("stategraph typeddict")
    check("小写 query 也能命中", len(r) == 1 and "StateGraph" in r[0]["title"], str(r))

    print("\n  —— 无结果时返回空列表而非报错 ——")
    r = search("量子计算")
    check("查不到的词：返回 []", r == [], str(r))
    r = search("foobarbaz")
    check("无意义字符串：返回 []", r == [], str(r))
    r = search("")
    check("空 query：返回 []（不抛异常）", r == [], str(r))
    r = search("   ")
    check("纯空格 query：返回 []（不抛异常）", r == [], str(r))

    print("\n  —— max_results 限制生效 ——")
    r = search("协议", max_results=1)
    check("max_results=1：只返回 1 条（命中 MCP 2 条但被截断）", len(r) == 1, str(len(r)))
    r = search("协议", max_results=0)
    check("max_results=0：返回 0 条（不报错）", r == [], str(r))
    r = search("协议", max_results=2)
    check("max_results=2：命中 MCP 2 条全部返回", len(r) == 2, str(len(r)))

    # 通过裸 search 函数测钳制（绕过 pydantic 的 0-10 范围）
    r = search("MCP", max_results=100)
    check("max_results=100 被钳到 HARD_MAX_RESULTS=10，不会越界",
          len(r) <= HARD_MAX_RESULTS and len(r) == 2, str(len(r)))
    r = search("MCP", max_results=-3)
    check("max_results=-3 被钳到 0，不报错", r == [], str(r))
    r = search("MCP", max_results=99999)
    check("max_results 极大值不会真的去切 99999 片", len(r) == 2, str(len(r)))

    print("\n  —— schema 形状 ——")
    sch = SEARCH_SCHEMA
    check("schema type=object", sch["type"] == "object")
    check("query 是 string", sch["properties"]["query"]["type"] == "string")
    check("query 是必填", "query" in sch["required"])
    check("max_results 是 integer", sch["properties"]["max_results"]["type"] == "integer")
    check("max_results 有默认值 5", sch["properties"]["max_results"]["default"] == 5)
    check("max_results 有 maximum=10", sch["properties"]["max_results"]["maximum"] == 10)
    check("max_results 有 minimum=0", sch["properties"]["max_results"]["minimum"] == 0)
    check("additionalProperties=false（挡住模型瞎塞字段）", sch.get("additionalProperties") is False)
    check("query 没有 enum（不限制模型问什么）", "enum" not in sch["properties"]["query"])
    check("query 没有 pattern（不限制格式）", "pattern" not in sch["properties"]["query"])

    spec = search_tool_spec()
    check("工具声明有 name=search", spec["function"]["name"] == "search")
    check("工具声明带 parameters", "parameters" in spec["function"])
    check("工具声明是 type=function", spec["type"] == "function")

    print("\n  —— data/fixtures.json 可被读出（外部文件有效）——")
    r = search("BM25")
    check("读 fixtures.json 命中 BM25 那条", len(r) == 1 and "BM25" in r[0]["title"], str(r))
    # 删 fixtures.json 临时验证退化路径
    import json
    from search import FIXTURE_PATH
    backup = None
    removed = False
    if FIXTURE_PATH.exists():
        backup = FIXTURE_PATH.read_text(encoding="utf-8")
        FIXTURE_PATH.unlink()
        removed = True
    try:
        r = search("MCP")
        check("删掉 fixtures.json 后退化到内置 FIXTURES，仍能命中", len(r) == 2, str(len(r)))
        r = search("量子计算")
        check("退化路径下无结果仍返回 []", r == [], str(r))
    finally:
        if removed and backup is not None:
            FIXTURE_PATH.write_text(backup, encoding="utf-8")
    check("fixtures.json 已还原", FIXTURE_PATH.exists())

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    h = client.get("/health").json()
    check("GET /health", h["ok"] is True and h["fixture_count"] == 5)
    check("GET /health 列出主题", set(h["topics"]) == {"MCP 协议", "RAG 优化", "LangGraph"})

    fx = client.get("/fixtures").json()
    check("GET /fixtures 返回 5 条", fx["count"] == 5)

    sch = client.get("/schema").json()
    check("GET /schema 返回 SEARCH_SCHEMA", sch["type"] == "object" and "query" in sch["properties"])

    tl = client.get("/tools").json()
    check("GET /tools 返回 search 工具声明", tl["tools"][0]["function"]["name"] == "search")

    body = client.post("/search", json={"query": "MCP 协议"}).json()
    check("POST /search 默认 max_results 命中 2 条", body["count"] == 2, str(body["count"]))
    check("POST /search 返回 query 回显", body["query"] == "MCP 协议")

    body = client.post("/search", json={"query": "量子计算"}).json()
    check("POST /search 查不到时 count=0 且 results=[]", body["count"] == 0 and body["results"] == [])

    body = client.post("/search", json={"query": "协议", "max_results": 1}).json()
    check("POST /search max_results=1 截断生效", body["count"] == 1, str(body["count"]))

    # 校验：max_results 越界 pydantic 直接 422（不会进入业务逻辑）
    r422 = client.post("/search", json={"query": "x", "max_results": 999})
    check("POST /search max_results=999 被 pydantic 拒绝（422）", r422.status_code == 422, str(r422.status_code))
    r422 = client.post("/search", json={"query": ""})
    check("POST /search 空 query 被 pydantic 拒绝（422）", r422.status_code == 422, str(r422.status_code))

    # GET / 返回页面
    ridx = client.get("/")
    check("GET / 返回 200", ridx.status_code == 200, str(ridx.status_code))
    check("GET / 返回 HTML（含 <html）", "<html" in ridx.text.lower(), "no html tag")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
