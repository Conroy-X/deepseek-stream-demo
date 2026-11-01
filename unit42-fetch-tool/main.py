"""Unit 42 · 抓网页正文 —— FastAPI 实验台 + 自检。

启动：
    /home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8042

自检（离线）：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from fetch import URL_MAP, fetch_and_clean, list_fixtures

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 42 · 抓网页正文", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class FetchIn(BaseModel):
    url: str = Field(min_length=1)


@app.get("/health", summary="后端存活 + fixture 概览")
def health() -> dict:
    return {
        "ok": True,
        "unit": "unit42-fetch-tool",
        "fixture_count": len(URL_MAP),
        "urls": sorted(URL_MAP.keys()),
    }


@app.get("/fixtures", summary="列出可抓的 URL")
def fixtures() -> dict:
    return {"count": len(URL_MAP), "items": list_fixtures()}


@app.post("/fetch", summary="抓一个 URL 并清洗")
def do_fetch(req: FetchIn) -> dict:
    try:
        return fetch_and_clean(req.url)
    except Exception as e:  # noqa: BLE001 - HTTP 层统一报 400
        raise HTTPException(400, str(e))


@app.get("/fetch", summary="GET 版本（页面用 query string）")
def do_fetch_get(url: str) -> dict:
    try:
        return fetch_and_clean(url)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


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

    print("Unit 42 · 抓网页正文 自检（离线）\n")

    print("  —— 三步清洗的顺序：先去噪音再去标签 ——")
    r = fetch_and_clean("https://example.com/mcp-intro")
    check("page1 标题正确", r["title"] == "MCP 协议入门", r["title"])
    check("page1 清洗后不含 <script>", "<script" not in r["text"].lower(), r["text"][:120])
    check("page1 清洗后不含 </script>", "</script" not in r["text"].lower())
    check("page1 清洗后不含 <nav", "<nav" not in r["text"].lower())
    check("page1 清洗后不含 </nav>", "</nav" not in r["text"].lower())
    check("page1 清洗后不含 <style", "<style" not in r["text"].lower())
    check("page1 清洗后不含广告文案「SuperSearch Pro」", "SuperSearch" not in r["text"], r["text"][:200])
    check("page1 清洗后不含广告文案「MCP 商业课」", "商业课" not in r["text"])
    check("page1 清洗后不含 JS 变量「_tracker」", "_tracker" not in r["text"])
    check("page1 清洗后不含 JS 函数「gtag」", "gtag" not in r["text"])
    check("page1 清洗后不含「dataLayer」", "dataLayer" not in r["text"])
    check("page1 清洗后不含导航「首页 / 文档 / 博客」连写", "首页 文档 博客" not in r["text"])
    check("page1 清洗后正文里出现「MCP」", "MCP" in r["text"])
    check("page1 清洗后正文里出现「JSON-RPC」", "JSON-RPC" in r["text"])
    check("page1 清洗后正文里出现「-32002」", "-32002" in r["text"])

    print("\n  —— 正文长度合理 ——")
    check("page1 raw_length > 0", r["raw_length"] > 0, str(r["raw_length"]))
    check("page1 length > 0", r["length"] > 0, str(r["length"]))
    check("page1 清洗后比原始短（剥了 script/style/nav/广告）", r["length"] < r["raw_length"],
          f"{r['length']} vs {r['raw_length']}")
    # page1 原始 ~2000 字符，正文 ~500 字符，剥掉的比例应该在合理区间
    ratio = r["length"] / r["raw_length"]
    check("page1 清洗后 / 原始 比例在 0.2 ~ 0.7（合理区间）", 0.2 <= ratio <= 0.7, f"{ratio:.3f}")

    print("\n  —— page2（侧栏 + 广告 + 表格）——")
    r2 = fetch_and_clean("https://example.com/rag-tuning")
    check("page2 标题正确", r2["title"] == "RAG 检索质量调优", r2["title"])
    check("page2 清洗后不含 <script", "<script" not in r2["text"].lower())
    check("page2 清洗后不含 <style", "<style" not in r2["text"].lower())
    check("page2 清洗后不含 sidebar 内容「相关阅读」", "相关阅读" not in r2["text"])
    check("page2 清洗后不含 promo「付费课」", "付费课" not in r2["text"])
    check("page2 清洗后不含广告文案「¥499」", "¥499" not in r2["text"])
    check("page2 清洗后不含 gtag", "gtag" not in r2["text"])
    check("page2 清洗后含正文「chunk 256 / 512 / 1024」", "chunk" in r2["text"] and "512" in r2["text"])
    check("page2 清洗后含「reranker」", "reranker" in r2["text"].lower() or "reranker" in r2["text"])
    check("page2 清洗后含「0.62」和「0.81」", "0.62" in r2["text"] and "0.81" in r2["text"])
    # 表格里数字应该保留
    check("page2 表格数字保留（90 / 120 / 180）", "90" in r2["text"] and "120" in r2["text"] and "180" in r2["text"])
    check("page2 不含 HTML 标签残余（<td / <tr 等）", "<td" not in r2["text"].lower() and "<tr" not in r2["text"].lower())

    print("\n  —— page3（无 article 容器，靠 nav + script + 残留 style 测）——")
    r3 = fetch_and_clean("https://example.com/langgraph-state")
    check("page3 标题正确", r3["title"] == "LangGraph StateGraph", r3["title"])
    check("page3 清洗后不含 <script", "<script" not in r3["text"].lower())
    check("page3 清洗后不含 <nav", "<nav" not in r3["text"].lower())
    check("page3 清洗后不含 </nav>", "</nav" not in r3["text"].lower())
    check("page3 清洗后不含「banner-ad」", "banner-ad" not in r3["text"])
    check("page3 清洗后不含广告文案「培训班 ¥199」", "培训班" not in r3["text"])
    check("page3 清洗后含正文「StateGraph」", "StateGraph" in r3["text"])
    check("page3 清洗后含正文「TypedDict」", "TypedDict" in r3["text"])
    check("page3 清洗后含正文「Annotated」", "Annotated" in r3["text"])
    check("page3 清洗后不含残留 style 内容「hidden-promo」", "hidden-promo" not in r3["text"])
    check("page3 清洗后不含 analytics.js 引用", "analytics.js" not in r3["text"])

    print("\n  —— page4（MCP isError，navbar + popup-ad + script）——")
    r4 = fetch_and_clean("https://example.com/mcp-error")
    check("page4 标题正确", r4["title"] == "MCP 工具失败 isError vs error", r4["title"])
    check("page4 清洗后不含 <script", "<script" not in r4["text"].lower())
    check("page4 清洗后不含 <nav", "<nav" not in r4["text"].lower())
    check("page4 清洗后不含 popup-ad 文案「调试课 ¥399」", "调试课" not in r4["text"])
    check("page4 清洗后不含 popup-ad 文案「周刊 ¥19」", "周刊" not in r4["text"])
    check("page4 清洗后不含 navbar 链接「首页 文档 API」", "首页 文档 API" not in r4["text"])
    check("page4 清洗后不含 JS 变量 _mt", "_mt" not in r4["text"])
    check("page4 清洗后含正文「isError」", "isError" in r4["text"])
    check("page4 清洗后含正文「-32602」", "-32602" in r4["text"])

    print("\n  —— page5（混合检索，topnav + sidebar-ad + script）——")
    r5 = fetch_and_clean("https://example.com/hybrid-search")
    check("page5 标题正确", r5["title"] == "混合检索 BM25 + 向量", r5["title"])
    check("page5 清洗后不含 <script", "<script" not in r5["text"].lower())
    check("page5 清洗后不含 <nav", "<nav" not in r5["text"].lower())
    check("page5 清洗后不含 sidebar-ad 文案「RAG 进阶课 ¥299」", "进阶课" not in r5["text"])
    check("page5 清洗后不含 topnav 链接「首页 搜索 关于」", "首页 搜索 关于" not in r5["text"])
    check("page5 清洗后不含 JS 函数 track", "track" not in r5["text"])
    check("page5 清洗后含正文「RRF」", "RRF" in r5["text"])
    check("page5 清洗后含正文「BM25」", "BM25" in r5["text"])
    check("page5 清洗后含正文「0.83」", "0.83" in r5["text"])

    print("\n  —— fixture 全覆盖 ——")
    check("URL 映射 5 个 URL", len(URL_MAP) == 5, str(len(URL_MAP)))
    for u in URL_MAP:
        r = fetch_and_clean(u)
        check(f"{u} 能抓", r["length"] > 0, f"len={r['length']}")
        check(f"{u} 返回 fixture 字段", r["fixture"].endswith(".html"), r["fixture"])

    print("\n  —— 错误处理 ——")
    from fetch import FetchError
    raised = False
    try:
        fetch_and_clean("https://example.com/not-in-fixture")
    except FetchError:
        raised = True
    check("URL 不在 fixture 抛 FetchError", raised)

    raised = False
    try:
        fetch_and_clean("")
    except FetchError:
        raised = True
    check("空 URL 抛 FetchError（不是返回空文本）", raised)

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    h = client.get("/health").json()
    check("GET /health", h["ok"] is True and h["fixture_count"] == 5)

    fx = client.get("/fixtures").json()
    check("GET /fixtures 返回 5 条", fx["count"] == 5)

    body = client.post("/fetch", json={"url": "https://example.com/mcp-intro"}).json()
    check("POST /fetch 返回正文", "MCP" in body["text"] and body["length"] > 0)

    body = client.get("/fetch?url=https://example.com/rag-tuning").json()
    check("GET /fetch?url= 返回正文", "reranker" in body["text"].lower() or "chunk" in body["text"])

    err = client.post("/fetch", json={"url": "https://example.com/missing"})
    check("POST /fetch 不存在的 URL 返回 400", err.status_code == 400, str(err.status_code))

    err = client.post("/fetch", json={"url": ""})
    check("POST /fetch 空 URL 被 pydantic 拒绝（422）", err.status_code == 422, str(err.status_code))

    ridx = client.get("/")
    check("GET / 返回 200", ridx.status_code == 200)
    check("GET / 返回 HTML", "<html" in ridx.text.lower())

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
