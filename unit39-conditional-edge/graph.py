"""Unit 39 · Conditional Edge：条件路由。

核心 API：`add_conditional_edges(source, router_fn, path_map)`。

router_fn 接收 state，返回一个字符串（路由 key）。
path_map 把路由 key 映射到目标节点名。

场景：
  - 简单问题（如「你好」）→ 直接回答，不走搜索
  - 复杂问题（如「MCP 是什么」）→ 走完整搜索流程
  - 查不到的问题 → 走兜底节点
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph


class State(TypedDict):
    question: str
    question_type: str      # simple / complex / unknown
    answer: str
    results: list[str]
    steps: list[str]


def classify_node(state: State) -> dict[str, Any]:
    """分类问题类型。这是条件路由的依据。"""
    q = state.get("question", "")
    if not q.strip():
        return {"question_type": "unknown", "steps": state.get("steps", []) + ["classify"]}
    # 离线规则：短问题 = simple，含问号的 = complex
    if len(q) < 10 and "？" not in q and "?" not in q:
        return {"question_type": "simple", "steps": state.get("steps", []) + ["classify"]}
    if any(kw in q for kw in ["MCP", "RAG", "LangGraph", "是什么", "怎么"]):
        return {"question_type": "complex", "steps": state.get("steps", []) + ["classify"]}
    return {"question_type": "unknown", "steps": state.get("steps", []) + ["classify"]}


def router_fn(state: State) -> str:
    """路由函数：返回 question_type 字段的值。"""
    return state.get("question_type", "unknown")


def simple_answer_node(state: State) -> dict[str, Any]:
    return {"answer": f"简单回答：{state.get('question', '')}", "steps": state.get("steps", []) + ["simple"]}


def search_node(state: State) -> dict[str, Any]:
    q = state.get("question", "")
    if "MCP" in q:
        results = ["MCP 是 JSON-RPC", "MCP 有三种传输"]
    elif "RAG" in q:
        results = ["RAG 受 chunk 影响", "Rerank 提升精度"]
    else:
        results = ["LangGraph 用 StateGraph"]
    return {"results": results, "steps": state.get("steps", []) + ["search"]}


def generate_node(state: State) -> dict[str, Any]:
    results = state.get("results", [])
    return {"answer": "复杂回答：" + "；".join(results), "steps": state.get("steps", []) + ["generate"]}


def fallback_node(state: State) -> dict[str, Any]:
    return {"answer": "我不知道怎么回答这个问题", "steps": state.get("steps", []) + ["fallback"]}


def build_conditional_graph():
    """带条件路由的图。

    classify → [simple → END, complex → search → generate → END, unknown → fallback → END]
    """
    g = StateGraph(State)
    g.add_node("classify", classify_node)
    g.add_node("simple", simple_answer_node)
    g.add_node("search", search_node)
    g.add_node("generate", generate_node)
    g.add_node("fallback", fallback_node)

    g.set_entry_point("classify")

    # 条件路由：router_fn 返回 "simple" / "complex" / "unknown"
    g.add_conditional_edges(
        "classify",
        router_fn,
        {
            "simple": "simple",
            "complex": "search",
            "unknown": "fallback",
        },
    )

    g.add_edge("simple", END)
    g.add_edge("search", "generate")
    g.add_edge("generate", END)
    g.add_edge("fallback", END)

    return g.compile()


def build_linear_graph():
    """无条件路由的线性图（对比用）：classify → search → generate → END。"""
    g = StateGraph(State)
    g.add_node("classify", classify_node)
    g.add_node("search", search_node)
    g.add_node("generate", generate_node)
    g.set_entry_point("classify")
    g.add_edge("classify", "search")
    g.add_edge("search", "generate")
    g.add_edge("generate", END)
    return g.compile()


def run_question(question: str) -> dict[str, Any]:
    app = build_conditional_graph()
    return app.invoke({"question": question})