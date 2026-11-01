"""Unit 37 · LangGraph State：用 TypedDict 定义 State，跑通单节点图。

LangGraph 的核心抽象是 **State**：一个 TypedDict，图的每个节点接收它、返回一个
partial dict（只含要更新的字段），框架自动 merge。

这和 Unit 34 手写的 while 循环有什么区别？
  手写循环：你自己管 messages 列表，每次 append。
  LangGraph：你只管「这一步要改什么」，框架帮你 merge。

启动：
    python main.py
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph


# ── State 定义 ───────────────────────────────────────────────────────────────


class ResearchState(TypedDict):
    """整个图共享的状态。

    TypedDict 不是运行时类型 —— 它只是给类型检查器看的。
    LangGraph 在运行时用 dict 操作，但靠 TypedDict 声明字段让开发者知道
    「这个图里有哪些状态」。

    为什么用 TypedDict 不用 dataclass：
    - TypedDict 的字段是可选的（可以只返回部分字段做 partial update）
    - dataclass 要求构造时传所有字段
    - LangGraph 的 merge 语义天然适配 dict 的「部分更新」
    """
    question: str           # 用户问题（入口设）
    query: str              # 改写后的查询（节点产出）
    results: list[str]      # 搜索结果（节点产出）
    answer: str             # 最终答案（节点产出）
    rounds: int             # 执行轮次（节点累加）


# ── 节点函数 ────────────────────────────────────────────────────────────────


def rewrite_node(state: ResearchState) -> dict[str, Any]:
    """把问题改写成搜索查询。只返回要更新的字段。"""
    q = state.get("question", "")
    # 离线规则：去掉问号、加引号
    query = q.replace("？", "").replace("?", "").strip()
    return {"query": f'"{query}"', "rounds": state.get("rounds", 0) + 1}


def search_node(state: ResearchState) -> dict[str, Any]:
    """按查询搜索（离线 fixture）。"""
    query = state.get("query", "")
    # 离线：按 query 里的关键词返回假结果
    if "MCP" in query or "协议" in query:
        results = ["MCP 是 JSON-RPC 协议", "MCP 有三种传输", "MCP 的能力协商"]
    elif "RAG" in query or "检索" in query:
        results = ["RAG 的召回率受 chunk 大小影响", "Rerank 能提升精度"]
    else:
        results = ["没有找到相关结果"]
    return {"results": results, "rounds": state.get("rounds", 0) + 1}


def answer_node(state: ResearchState) -> dict[str, Any]:
    """根据搜索结果生成答案。"""
    results = state.get("results", [])
    if not results:
        return {"answer": "无法回答", "rounds": state.get("rounds", 0) + 1}
    answer = "根据搜索：" + "；".join(results[:2])
    return {"answer": answer, "rounds": state.get("rounds", 0) + 1}


# ── 构建图 ──────────────────────────────────────────────────────────────────


def build_single_node_graph():
    """单节点图：只有一个节点，跑完就结束。

    最小可跑骨架。用来验证 StateGraph 的 API：
      StateGraph(StateType) → add_node → set_entry_point → add_edge(END) → compile → invoke
    """
    graph = StateGraph(ResearchState)
    graph.add_node("rewrite", rewrite_node)
    graph.set_entry_point("rewrite")
    graph.add_edge("rewrite", END)
    return graph.compile()


def build_three_node_graph():
    """三节点图：rewrite → search → answer。

    每个 node 只返回 partial dict，框架自动 merge。
    最终 state 包含所有字段。
    """
    graph = StateGraph(ResearchState)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("search", search_node)
    graph.add_node("answer", answer_node)
    graph.set_entry_point("rewrite")
    graph.add_edge("rewrite", "search")
    graph.add_edge("search", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def build_noop_graph():
    """空操作节点：返回空 dict，state 不变。

    用来证明 merge 语义：返回 {} 意味着「这一步不改任何东西」。
    """
    def noop(state: ResearchState) -> dict[str, Any]:
        return {}

    graph = StateGraph(ResearchState)
    graph.add_node("noop", noop)
    graph.set_entry_point("noop")
    graph.add_edge("noop", END)
    return graph.compile()


def build_overwrite_graph():
    """覆盖 vs merge：返回的字段会覆盖旧值，没返回的字段保留。

    这是 LangGraph 的默认行为 —— 不是 deep merge，是 top-level key merge。
    """
    def set_a(state: ResearchState) -> dict[str, Any]:
        return {"question": "被改写了", "rounds": 1}

    def keep_b(state: ResearchState) -> dict[str, Any]:
        # 不返回 question，question 保持上一步的值
        return {"answer": "我补了答案", "rounds": 2}

    graph = StateGraph(ResearchState)
    graph.add_node("set_a", set_a)
    graph.add_node("keep_b", keep_b)
    graph.set_entry_point("set_a")
    graph.add_edge("set_a", "keep_b")
    graph.add_edge("keep_b", END)
    return graph.compile()


# ── 对比手写循环（Unit 34 的模式）─────────────────────────────────────────


def handwritten_loop(question: str) -> dict[str, Any]:
    """手写 while 循环做同样的事。用来对比 LangGraph 的抽象层级。"""
    state: dict[str, Any] = {"question": question, "rounds": 0}

    # 手写：每步显式更新 state
    state.update(rewrite_node(state))  # type: ignore[arg-type]
    state.update(search_node(state))   # type: ignore[arg-type]
    state.update(answer_node(state))   # type: ignore[arg-type]

    return state


# ── 给页面用的执行追踪 ──────────────────────────────────────────────────────


def trace_execution(question: str) -> dict[str, Any]:
    """跑一遍三节点图，返回每步的 state 快照。"""
    app = build_three_node_graph()

    # LangGraph 不直接暴露每步中间 state，我们用 stream 模式
    initial = {"question": question, "rounds": 0}
    final = app.invoke(initial)

    # 对比手写循环
    hw = handwritten_loop(question)

    return {
        "input": initial,
        "final": final,
        "handwritten": hw,
        "same_answer": final.get("answer") == hw.get("answer"),
        "same_rounds": final.get("rounds") == hw.get("rounds"),
    }