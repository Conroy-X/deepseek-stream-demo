"""Unit 45 · Research Agent —— 用 LangGraph StateGraph 串起 search→fetch→extract→analyze→report。

教学核心：**把工具链编成图而不是链**。

LangGraph 的 `StateGraph` 用 `TypedDict` 定义状态，每个节点返回增量（只写改了的字段），
框架自动 merge。比 LangChain 的链式 Runnable 更适合多分支：节点可以失败、回退、并行。

本 Agent 的图：

    START
      ↓
    search        ← 在 unit41 fixture 里按 query 搜
      ↓
    fetch         ← 把每条结果的 URL 喂给 unit42 fetch_and_clean
      ↓
    extract       ← 对每篇清洗后正文用 unit43 OfflineModel 提取要点
      ↓
    analyze       ← 把每篇的要点塞进 unit44 cross_compare
      ↓
    report        ← 渲染成带来源引用的 Markdown
      ↓
    END

每个节点只改自己负责的字段（增量返回），不读不改别人的字段。
这是 LangGraph 比手写 while 循环的优势：状态合并是框架的事，节点只关心业务。
"""

from __future__ import annotations

import os
import sys
from typing import Any, TypedDict

# 把兄弟单元加到 path
UNIT_DIR = os.path.dirname(os.path.abspath(__file__))
JOURNEY_DIR = os.path.dirname(UNIT_DIR)
for sub in ("unit41-search-tool", "unit42-fetch-tool", "unit43-extract", "unit44-analysis"):
    p = os.path.join(JOURNEY_DIR, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from langgraph.graph import END, START, StateGraph

from search import search as do_search  # type: ignore[import-not-found]
from fetch import fetch_and_clean, FetchError  # type: ignore[import-not-found]
from extract import extract_key_points, OfflineModel, verify_sourcing  # type: ignore[import-not-found]
from analyzer import cross_compare  # type: ignore[import-not-found]


# Agent 抓要点时的上限：拉到 10 让带 delta_pair 的句子（数值句）能进 top N，
# 否则 definition 句子优先级太高把冲突材料挤出去。
EXTRACT_MAX_POINTS = 10


# ---------------------------------------------------------------------------
# 状态：所有节点共享的「黑板」
# ---------------------------------------------------------------------------
class ResearchState(TypedDict, total=False):
    """每个节点只写自己负责的字段。total=False 让字段都可选。"""
    topic: str               # 用户输入主题（search 用）
    max_results: int         # 搜索上限
    search_results: list[dict[str, str]]   # search 输出
    fetched: list[dict[str, Any]]          # fetch 输出：{url, title, text, ok, error}
    extracted: list[dict[str, Any]]        # extract 输出：{url, points}
    analysis: dict[str, Any]               # analyze 输出：cross_compare 结果
    report: str             # report 输出：Markdown 文本
    error: str              # 任一节点失败时的兜底


# ---------------------------------------------------------------------------
# 节点：每个节点是一个 (state) -> dict 的纯函数
# ---------------------------------------------------------------------------
def node_search(state: ResearchState) -> dict[str, Any]:
    """节点 1：根据 topic 在 fixture 里搜。"""
    topic = (state.get("topic") or "").strip()
    if not topic:
        return {"error": "topic 不能为空", "search_results": []}
    max_results = state.get("max_results") or 5
    results = do_search(topic, max_results=max_results)
    return {"search_results": results, "error": "" if results else ""}


def node_fetch(state: ResearchState) -> dict[str, Any]:
    """节点 2：把每条搜索结果的 URL 抓下来清洗。

    单条失败不掀翻整个 Agent —— URL 不在 fixture / 文件缺失就标 ok=False 跳过。
    """
    fetched: list[dict[str, Any]] = []
    for r in state.get("search_results") or []:
        url = r.get("url", "")
        try:
            data = fetch_and_clean(url)
            fetched.append({
                "url": url,
                "title": data["title"],
                "text": data["text"],
                "length": data["length"],
                "ok": True,
                "error": "",
            })
        except FetchError as e:
            fetched.append({"url": url, "title": "", "text": "", "length": 0, "ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001 - 兜底
            fetched.append({"url": url, "title": "", "text": "", "length": 0, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return {"fetched": fetched}


def node_extract(state: ResearchState) -> dict[str, Any]:
    """节点 3：对每篇清洗后正文提取要点。

    用 unit43 的 `extract_key_points`（规则版）+ `OfflineModel` 包装。
    max_points 拉到 10，避免 definition 句子优先级太高把带 delta_pair 的数值句挤出去 ——
    那种句子是 unit44 冲突检测的材料，丢了就检测不到冲突。
    """
    model = OfflineModel()
    extracted: list[dict[str, Any]] = []
    for f in state.get("fetched") or []:
        if not f.get("ok") or not f.get("text"):
            extracted.append({"url": f.get("url", ""), "points": []})
            continue
        # 直接调 extract_key_points，绕过 OfflineModel.complete 的默认 max=5
        points = extract_key_points(f["text"], max_points=EXTRACT_MAX_POINTS)
        # 走一遍 verify_sourcing 兜底（规则版天然 verbatim，但保留运行时校验）
        sv = verify_sourcing(f["text"], points)
        if not all(s["found"] for s in sv):
            # 找不到源句的丢掉（防止 hallucination 字段漏到下游）
            points = [p for p, s in zip(points, sv) if s["found"]]
        # 同时记一次模型调用（让 model.calls 反映真实使用）
        model.complete([{"role": "user", "content": f["text"]}])
        extracted.append({"url": f.get("url", ""), "points": points})
    return {"extracted": extracted}


def node_analyze(state: ResearchState) -> dict[str, Any]:
    """节点 4：把每篇的要点拼成 source 喂给 cross_compare。

    cross_compare 接受 `[{name, text}]`。我们只用 `source_sentence`（verbatim）
    拼 text —— 不把 `point` 字段也拼进去，否则同一 delta_pair 会出现两次（一次在 point，
    一次在 source_sentence），冲突值会被算两次。
    URL 拼在 text 最前面，让 analyzer 的主题检测能命中（URL 含主题词如 mcp / rag）。
    """
    sources: list[dict[str, str]] = []
    for e in state.get("extracted") or []:
        url = e.get("url", "")
        points = e.get("points") or []
        if not points:
            continue
        text = f"来源：{url}\n" + "\n".join(p["source_sentence"] for p in points)
        sources.append({"name": url, "text": text})
    if len(sources) < 2:
        # 不足 2 个来源没法交叉比对
        return {"analysis": {
            "topic": state.get("topic", ""),
            "sources_count": len(sources),
            "consensus": [],
            "conflicts": [],
            "unique_points": [],
            "sources_summary": [],
            "note": "来源不足 2 个，跳过交叉比对",
        }}
    result = cross_compare(sources)
    return {"analysis": result}


def node_report(state: ResearchState) -> dict[str, str]:
    """节点 5：把全部状态渲染成带来源引用的 Markdown。"""
    report = render_report(state)
    return {"report": report}


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------
def render_report(state: ResearchState) -> str:
    topic = state.get("topic") or ""
    err = state.get("error") or ""
    if err:
        return f"# 研究报告：{topic}\n\n> 错误：{err}\n"

    search_results = state.get("search_results") or []
    fetched = state.get("fetched") or []
    extracted = state.get("extracted") or []
    analysis = state.get("analysis") or {}

    lines: list[str] = []
    lines.append(f"# 研究报告：{topic}")
    lines.append("")
    lines.append(f"> 自动生成 · 搜索 {len(search_results)} 条 / 抓取 {len(fetched)} 篇 / 提取要点 {sum(len(e.get('points', [])) for e in extracted)} 个")
    lines.append("")

    # 来源清单
    lines.append("## 来源")
    for i, r in enumerate(search_results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        lines.append(f"{i}. [{title}]({url})")
    lines.append("")

    # 抓取状态
    ok_count = sum(1 for f in fetched if f.get("ok"))
    lines.append(f"## 抓取状态（{ok_count}/{len(fetched)} 成功）")
    for f in fetched:
        url = f.get("url", "")
        if f.get("ok"):
            lines.append(f"- ✓ {url} · 正文 {f.get('length', 0)} 字符")
        else:
            lines.append(f"- ✗ {url} · 失败：{f.get('error', '')}")
    lines.append("")

    # 要点
    lines.append("## 各来源要点")
    for e in extracted:
        url = e.get("url", "")
        points = e.get("points") or []
        if not points:
            lines.append(f"### {url}")
            lines.append("（无可提取要点）")
            lines.append("")
            continue
        lines.append(f"### {url}")
        for i, p in enumerate(points, 1):
            lines.append(f"{i}. {p['point']}")
            lines.append(f"   - 源句：{p['source_sentence']}")
        lines.append("")

    # 交叉比对
    if analysis:
        lines.append("## 交叉比对")
        consensus = analysis.get("consensus") or []
        conflicts = analysis.get("conflicts") or []
        unique_points = analysis.get("unique_points") or []
        note = analysis.get("note")
        if note:
            lines.append(f"_{note}_")
            lines.append("")
        lines.append(f"### 共识（{len(consensus)}）")
        for c in consensus:
            lines.append(f"- {c.get('claim', '')}（来源：{', '.join(c.get('sources', []))}）")
        lines.append("")
        lines.append(f"### 冲突（{len(conflicts)}）")
        if not conflicts:
            lines.append("- 无")
        for c in conflicts:
            lines.append(f"- **{c.get('claim', '')}**")
            for v in c.get("values", []):
                lines.append(f"  - {v.get('value')} ← {', '.join(v.get('sources', []))}")
        lines.append("")
        lines.append(f"### 补充点（{len(unique_points)}）")
        if not unique_points:
            lines.append("- 无")
        for u in unique_points:
            lines.append(f"- {u.get('claim', '')}")
        lines.append("")

    lines.append("---")
    lines.append("_由 unit45 Research Agent 自动生成 · 离线 fixture · 不联网_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 构图
# ---------------------------------------------------------------------------
def build_graph():
    """构造 StateGraph 并编译。

    节点固定顺序：search → fetch → extract → analyze → report。
    本单元不做条件路由（教学版），但 StateGraph 结构支持后续扩展。
    """
    g = StateGraph(ResearchState)
    g.add_node("search", node_search)
    g.add_node("fetch", node_fetch)
    g.add_node("extract", node_extract)
    g.add_node("analyze", node_analyze)
    g.add_node("report", node_report)
    g.add_edge(START, "search")
    g.add_edge("search", "fetch")
    g.add_edge("fetch", "extract")
    g.add_edge("extract", "analyze")
    g.add_edge("analyze", "report")
    g.add_edge("report", END)
    return g.compile()


# 模块级编译：import 一次，多次复用
COMPILED = build_graph()


def run_research(topic: str, max_results: int = 5) -> dict[str, Any]:
    """端到端跑一遍。返回最终 state（含 report 字段）。"""
    if not topic or not topic.strip():
        return {
            "topic": topic,
            "search_results": [],
            "fetched": [],
            "extracted": [],
            "analysis": {},
            "report": "# 研究报告\n\n> 错误：topic 不能为空\n",
            "error": "topic 不能为空",
        }
    init: ResearchState = {"topic": topic.strip(), "max_results": max_results}
    return COMPILED.invoke(init)
