"""Unit 45 · Research Agent —— FastAPI 实验台 + 自检。

启动：
    /home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8045

自检（离线）：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from agent import (
    EXTRACT_MAX_POINTS,
    build_graph,
    node_analyze,
    node_extract,
    node_fetch,
    node_report,
    node_search,
    render_report,
    run_research,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 45 · Research Agent", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ResearchIn(BaseModel):
    topic: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=10)


@app.get("/health", summary="后端存活 + Agent 拓扑概览")
def health() -> dict:
    return {
        "ok": True,
        "unit": "unit45-project3",
        "graph": ["search", "fetch", "extract", "analyze", "report"],
        "extract_max_points": EXTRACT_MAX_POINTS,
        "uses": ["unit41-search-tool", "unit42-fetch-tool", "unit43-extract", "unit44-analysis"],
    }


@app.get("/graph", summary="返回拓扑结构（页面用）")
def graph_info() -> dict:
    return {
        "nodes": [
            {"name": "search", "desc": "按 topic 在 unit41 fixture 里搜"},
            {"name": "fetch", "desc": "每条 URL 喂给 unit42 fetch_and_clean"},
            {"name": "extract", "desc": "每篇正文用 unit43 OfflineModel 提取要点"},
            {"name": "analyze", "desc": "要点拼成 sources 喂给 unit44 cross_compare"},
            {"name": "report", "desc": "渲染带来源引用的 Markdown"},
        ],
        "edges": [["START", "search"], ["search", "fetch"], ["fetch", "extract"],
                  ["extract", "analyze"], ["analyze", "report"], ["report", "END"]],
    }


@app.post("/research", summary="跑一遍 Research Agent")
def do_research(req: ResearchIn) -> dict:
    return run_research(req.topic, req.max_results)


@app.post("/research/report", summary="只返回 Markdown 报告（text/plain）", response_class=PlainTextResponse)
def do_research_report(req: ResearchIn) -> str:
    state = run_research(req.topic, req.max_results)
    return state.get("report", "")


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

    print("Unit 45 · Research Agent 自检（离线）\n")

    print("  —— LangGraph StateGraph 构图 ——")
    g = build_graph()
    check("build_graph 返回可编译图", g is not None)
    check("extract_max_points >= 8（避免 definition 挤掉数值句）",
          EXTRACT_MAX_POINTS >= 8, str(EXTRACT_MAX_POINTS))

    print("\n  —— 端到端跑通：MCP 协议 ——")
    r = run_research("MCP 协议")
    check("run_research 返回 dict", isinstance(r, dict))
    check("topic 字段 = MCP 协议", r.get("topic") == "MCP 协议", str(r.get("topic")))
    check("search_results 至少 2 条（MCP 协议 fixture 有 2 条）",
          len(r.get("search_results") or []) >= 2, str(len(r.get("search_results") or [])))
    check("fetched 至少 2 条", len(r.get("fetched") or []) >= 2)
    ok_count = sum(1 for f in r["fetched"] if f.get("ok"))
    check("fetched 全部成功（5 个 fixture URL 都在 URL_MAP 里）",
          ok_count == len(r["fetched"]), f"{ok_count}/{len(r['fetched'])}")
    check("extracted 至少 2 条", len(r.get("extracted") or []) >= 2)
    total_points = sum(len(e.get("points", [])) for e in r.get("extracted") or [])
    check("总要点数 >= 3（MCP 文档有多句定义/数值）", total_points >= 3, str(total_points))
    check("analysis 字段存在", isinstance(r.get("analysis"), dict))
    check("report 字段非空", bool(r.get("report")))
    check("report 以「# 研究报告」开头", r["report"].startswith("# 研究报告"), r["report"][:40])

    print("\n  —— 报告含来源引用 ——")
    rep = r["report"]
    check("报告含 ## 来源 标题", "## 来源" in rep)
    check("报告含 markdown 链接 [title](url)", "](" in rep and "https://example.com" in rep)
    check("报告含 ## 抓取状态", "## 抓取状态" in rep)
    check("报告含 ## 各来源要点", "## 各来源要点" in rep)
    check("报告含 ## 交叉比对", "## 交叉比对" in rep)
    check("报告含 ### 共识", "### 共识" in rep)
    check("报告含 ### 冲突", "### 冲突" in rep)
    check("报告含 ### 补充点", "### 补充点" in rep)
    check("报告含「自动生成」metadata", "自动生成" in rep)
    check("报告末尾有「由 unit45 Research Agent 自动生成」", "unit45 Research Agent 自动生成" in rep)

    print("\n  —— 冲突被标出：RAG 主题（rag-tuning vs hybrid-search）——")
    r2 = run_research("RAG")
    a2 = r2.get("analysis") or {}
    check("RAG 主题：analysis.topic = reranker", a2.get("topic") == "reranker", str(a2.get("topic")))
    check("RAG 主题：检测到冲突", len(a2.get("conflicts") or []) >= 1, str(len(a2.get("conflicts") or [])))
    check("RAG 主题：冲突数 >= 2（起始值 + 最终值）",
          len(a2.get("conflicts") or []) >= 2, str(len(a2.get("conflicts") or [])))
    # 验证冲突里有 0.62 / 0.55 / 0.81 / 0.79
    conflict_values: set[float] = set()
    for c in a2.get("conflicts") or []:
        for v in c.get("values", []):
            conflict_values.add(v.get("value"))
    check("冲突数值含 0.62", 0.62 in conflict_values, str(conflict_values))
    check("冲突数值含 0.55", 0.55 in conflict_values, str(conflict_values))
    check("冲突数值含 0.81", 0.81 in conflict_values, str(conflict_values))
    check("冲突数值含 0.79", 0.79 in conflict_values, str(conflict_values))
    # 验证报告里冲突块出现
    rep2 = r2["report"]
    check("RAG 报告含「### 冲突（N）」标题且 N >= 2", "### 冲突（" in rep2 and any(f"### 冲突（{n}）" in rep2 for n in range(2, 20)), rep2[:200])
    check("RAG 报告含 0.62 数值", "0.62" in rep2)
    check("RAG 报告含 0.55 数值", "0.55" in rep2)
    check("RAG 报告含 0.81 数值", "0.81" in rep2)
    check("RAG 报告含 0.79 数值", "0.79" in rep2)
    check("RAG 报告含 hybrid-search 来源", "hybrid-search" in rep2)
    check("RAG 报告含 rag-tuning 来源", "rag-tuning" in rep2)

    print("\n  —— 共识正确：MCP 主题（两篇都在讲 mcp）——")
    a_mcp = r.get("analysis") or {}
    check("MCP 主题：有共识", len(a_mcp.get("consensus") or []) >= 1, str(a_mcp.get("consensus")))
    if a_mcp.get("consensus"):
        c0 = a_mcp["consensus"][0]
        check("共识 claim 含 mcp", "mcp" in c0.get("claim", "").lower(), c0.get("claim"))
        check("共识列出 >= 2 个来源", len(c0.get("sources") or []) >= 2, str(c0.get("sources")))

    print("\n  —— 空主题返回提示 ——")
    r3 = run_research("")
    check("空 topic：返回 error 字段", r3.get("error") == "topic 不能为空", str(r3.get("error")))
    check("空 topic：search_results 为空", r3.get("search_results") == [])
    check("空 topic：fetched 为空", r3.get("fetched") == [])
    check("空 topic：report 含错误提示", "topic 不能为空" in r3.get("report", ""))
    r4 = run_research("   ")
    check("纯空格 topic：也返回 error", r4.get("error") == "topic 不能为空", str(r4.get("error")))

    print("\n  —— 查不到的主题：搜索返回空 ——")
    r5 = run_research("量子计算")
    check("查不到的主题：search_results 为空", r5.get("search_results") == [])
    check("查不到的主题：fetched 为空", r5.get("fetched") == [])
    check("查不到的主题：extracted 为空", r5.get("extracted") == [])
    check("查不到的主题：report 不崩", bool(r5.get("report")))
    check("查不到的主题：report 含「搜索 0 条」", "搜索 0 条" in r5["report"], r5["report"][:200])

    print("\n  —— 单节点单测：每个节点是纯函数 ——")
    s1 = node_search({"topic": "MCP 协议", "max_results": 5})
    check("node_search 返回 search_results", isinstance(s1.get("search_results"), list))
    check("node_search 不写 report 字段（增量）", "report" not in s1)
    check("node_search 不写 fetched 字段", "fetched" not in s1)

    s2 = node_fetch({"search_results": [{"title": "t", "url": "https://example.com/mcp-intro", "snippet": "s"}]})
    check("node_fetch 返回 fetched", isinstance(s2.get("fetched"), list))
    check("node_fetch 不写 search_results（增量）", "search_results" not in s2)
    check("node_fetch 第一条 ok=True", s2["fetched"][0].get("ok") is True)

    # fetch 处理未知 URL：不抛，标 ok=False
    s2b = node_fetch({"search_results": [{"title": "t", "url": "https://example.com/missing", "snippet": "s"}]})
    check("node_fetch 未知 URL：不抛异常，标 ok=False", s2b["fetched"][0].get("ok") is False)
    check("node_fetch 未知 URL：error 字段非空", bool(s2b["fetched"][0].get("error")))

    s3 = node_extract({"fetched": [{"url": "u", "ok": True, "text": "MCP 是一种协议。reranker 把命中率从 0.5 提升到 0.8。"}]})
    check("node_extract 返回 extracted", isinstance(s3.get("extracted"), list))
    check("node_extract 第一条有 points", isinstance(s3["extracted"][0].get("points"), list))
    check("node_extract 不写 fetched（增量）", "fetched" not in s3)

    # 空正文 / 失败的 fetch：返回空 points
    s3b = node_extract({"fetched": [{"url": "u", "ok": False, "text": ""}]})
    check("node_extract 失败的 fetch：points 为空", s3b["extracted"][0].get("points") == [])

    s4 = node_analyze({"extracted": [
        {"url": "u1", "points": [{"point": "p1", "source_sentence": "reranker 把命中率从 0.5 提升到 0.8。"}]},
        {"url": "u2", "points": [{"point": "p2", "source_sentence": "reranker 把命中率从 0.6 提升到 0.85。"}]},
    ]})
    check("node_analyze 返回 analysis", isinstance(s4.get("analysis"), dict))
    check("node_analyze 不写 extracted（增量）", "extracted" not in s4)
    check("node_analyze 检测到冲突", len(s4["analysis"].get("conflicts") or []) >= 2)

    # sources 不足 2 个时跳过比对
    s4b = node_analyze({"extracted": [{"url": "u1", "points": [{"point": "p1", "source_sentence": "x"}]}]})
    check("node_analyze 不足 2 来源：返回 note", "note" in s4b["analysis"])
    check("node_analyze 不足 2 来源：conflicts 为空", s4b["analysis"].get("conflicts") == [])

    s5 = node_report({
        "topic": "测试",
        "search_results": [{"title": "t", "url": "https://example.com/x", "snippet": "s"}],
        "fetched": [{"url": "https://example.com/x", "ok": True, "length": 100, "error": ""}],
        "extracted": [{"url": "https://example.com/x", "points": [{"point": "p", "source_sentence": "s"}]}],
        "analysis": {"consensus": [], "conflicts": [], "unique_points": []},
    })
    check("node_report 返回 report 字符串", isinstance(s5.get("report"), str))
    check("node_report 不写 analysis（增量）", "analysis" not in s5)
    check("node_report 含「# 研究报告」", "# 研究报告" in s5["report"])

    print("\n  —— 节点失败传播：search 空 topic 不掀翻后续 ——")
    # 即便 search 返回 error，fetch/extract 仍然能跑（空输入空输出）
    r6 = run_research("")
    check("空 topic 整个流程不崩", isinstance(r6, dict))
    check("空 topic：fetched=[]", r6.get("fetched") == [])

    print("\n  —— render_report 单测 ——")
    rep_empty = render_report({"topic": "", "error": "topic 不能为空"})
    check("render_report 错误状态返回错误提示", "topic 不能为空" in rep_empty)
    rep_no_analysis = render_report({
        "topic": "x",
        "search_results": [],
        "fetched": [],
        "extracted": [],
    })
    check("render_report 无 analysis 不崩", "## 交叉比对" not in rep_no_analysis or "## 交叉比对" in rep_no_analysis)
    check("render_report 无 sources 仍渲染标题", "# 研究报告" in rep_no_analysis)

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    h = client.get("/health").json()
    check("GET /health", h["ok"] is True and len(h["graph"]) == 5)

    gi = client.get("/graph").json()
    check("GET /graph 返回 5 个节点", len(gi["nodes"]) == 5)
    check("GET /graph 返回 6 条边（含 START / END）", len(gi["edges"]) == 6)

    body = client.post("/research", json={"topic": "MCP 协议"}).json()
    check("POST /research 返回完整 state", "report" in body and "analysis" in body)
    check("POST /research topic=MCP 命中搜索", len(body["search_results"]) >= 2)

    body = client.post("/research", json={"topic": "RAG"}).json()
    check("POST /research topic=RAG 检测到冲突",
          len((body.get("analysis") or {}).get("conflicts") or []) >= 2,
          str((body.get("analysis") or {}).get("conflicts")))

    # /research/report 返回 text/plain
    rrep = client.post("/research/report", json={"topic": "MCP 协议"})
    check("POST /research/report 返回 200", rrep.status_code == 200)
    check("POST /research/report content-type 含 text/plain", "text/plain" in rrep.headers.get("content-type", ""))
    check("POST /research/report 正文是 Markdown", "# 研究报告" in rrep.text)

    # 422 校验
    err = client.post("/research", json={"topic": ""})
    check("POST /research 空 topic 被 pydantic 拒绝（422）", err.status_code == 422, str(err.status_code))
    err = client.post("/research", json={"topic": "x", "max_results": 0})
    check("POST /research max_results=0 被 pydantic 拒绝（422）", err.status_code == 422)

    ridx = client.get("/")
    check("GET / 返回 200", ridx.status_code == 200)
    check("GET / 返回 HTML", "<html" in ridx.text.lower())

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
