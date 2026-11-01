"""Unit 38 · Node / Edge：多节点工作流 + stream 模式。

比 Unit 37 多两个节点（rerank + cite），并引入 stream 模式看每步中间 state。

节点设计原则：**每个节点只做一件事**。
  rewrite  — 改写问题
  search   — 搜索（离线 fixture）
  rerank   — 重排结果
  generate — 生成答案
  cite     — 加引用标注
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph


class ResearchState(TypedDict):
    question: str
    query: str
    raw_results: list[dict[str, str]]
    ranked_results: list[dict[str, str]]
    answer: str
    cited_answer: str
    steps: list[str]


def rewrite_node(state: ResearchState) -> dict[str, Any]:
    q = state.get("question", "").replace("？", "").replace("?", "").strip()
    return {"query": q, "steps": state.get("steps", []) + ["rewrite"]}


def search_node(state: ResearchState) -> dict[str, Any]:
    query = state.get("query", "")
    if "MCP" in query:
        raw = [{"title": "MCP 协议", "snippet": "JSON-RPC 2.0", "url": "doc://mcp"}, {"title": "MCP 传输", "snippet": "stdio 与 HTTP", "url": "doc://transport"}]
    elif "RAG" in query:
        raw = [{"title": "RAG 召回", "snippet": "chunk 大小影响召回", "url": "doc://rag1"}, {"title": "Rerank", "snippet": "重排提升精度", "url": "doc://rag2"}]
    else:
        raw = [{"title": "无结果", "snippet": "没找到", "url": "doc://empty"}]
    return {"raw_results": raw, "steps": state.get("steps", []) + ["search"]}


def rerank_node(state: ResearchState) -> dict[str, Any]:
    raw = state.get("raw_results", [])
    # 离线重排：按 snippet 长度排序（长 = 信息多）
    ranked = sorted(raw, key=lambda x: len(x.get("snippet", "")), reverse=True)
    return {"ranked_results": ranked, "steps": state.get("steps", []) + ["rerank"]}


def generate_node(state: ResearchState) -> dict[str, Any]:
    ranked = state.get("ranked_results", [])
    if not ranked:
        return {"answer": "无法回答", "steps": state.get("steps", []) + ["generate"]}
    snippets = [r["snippet"] for r in ranked[:2]]
    return {"answer": "答案：" + "；".join(snippets), "steps": state.get("steps", []) + ["generate"]}


def cite_node(state: ResearchState) -> dict[str, Any]:
    answer = state.get("answer", "")
    ranked = state.get("ranked_results", [])
    cites = "".join(f"[{i+1}]" for i in range(min(len(ranked), 2)))
    return {"cited_answer": f"{answer} {cites}", "steps": state.get("steps", []) + ["cite"]}


def build_graph():
    g = StateGraph(ResearchState)
    g.add_node("rewrite", rewrite_node)
    g.add_node("search", search_node)
    g.add_node("rerank", rerank_node)
    g.add_node("generate", generate_node)
    g.add_node("cite", cite_node)
    g.set_entry_point("rewrite")
    g.add_edge("rewrite", "search")
    g.add_edge("search", "rerank")
    g.add_edge("rerank", "generate")
    g.add_edge("generate", "cite")
    g.add_edge("cite", END)
    return g.compile()


def build_skip_rerank_graph():
    """跳过 rerank 的变体：search → generate → cite。用来对比「有重排 vs 没重排」。"""
    g = StateGraph(ResearchState)
    g.add_node("rewrite", rewrite_node)
    g.add_node("search", search_node)
    g.add_node("generate", generate_node)
    g.add_node("cite", cite_node)
    g.set_entry_point("rewrite")
    g.add_edge("rewrite", "search")
    g.add_edge("search", "generate")
    g.add_edge("generate", "cite")
    g.add_edge("cite", END)
    return g.compile()


def run_with_stream(question: str) -> dict[str, Any]:
    """用 stream 模式跑，返回每步的中间 state。"""
    app = build_graph()
    initial = {"question": question}
    steps = []
    for chunk in app.stream(initial, {"recursion_limit": 10}):
        # stream 每次返回 {node_name: update_dict}
        for node_name, update in chunk.items():
            steps.append({"node": node_name, "update": update})
    final = app.invoke(initial, {"recursion_limit": 10})
    return {"steps": steps, "final": final, "step_count": len(steps)}