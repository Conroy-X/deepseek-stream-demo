"""Unit 40 · Checkpoint / Memory：用 MemorySaver 持久化图的执行状态。

核心：
  - MemorySaver checkpointer：图的每一步 state 都被存下来
  - thread_id：标识一个会话，同 thread 可以恢复/继续执行
  - interrupt_before：在指定节点前暂停（人工审核 / HITL）
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver


class State(TypedDict):
    question: str
    query: str
    answer: str
    steps: list[str]


def rewrite_node(state: State) -> dict[str, Any]:
    q = state.get("question", "").replace("？", "").replace("?", "").strip()
    return {"query": q, "steps": state.get("steps", []) + ["rewrite"]}


def search_node(state: State) -> dict[str, Any]:
    return {"steps": state.get("steps", []) + ["search"]}


def generate_node(state: State) -> dict[str, Any]:
    return {"answer": f"回答：{state.get('query', '')}", "steps": state.get("steps", []) + ["generate"]}


def build_graph_with_checkpoint(interrupt_before: str | None = None):
    """带 checkpoint 的图。

    interrupt_before：在指定节点前暂停。用来做人工审核。
    """
    checkpointer = MemorySaver()
    g = StateGraph(State)
    g.add_node("rewrite", rewrite_node)
    g.add_node("search", search_node)
    g.add_node("generate", generate_node)
    g.set_entry_point("rewrite")
    g.add_edge("rewrite", "search")
    g.add_edge("search", "generate")
    g.add_edge("generate", END)

    if interrupt_before:
        return g.compile(checkpointer=checkpointer, interrupt_before=[interrupt_before])
    return g.compile(checkpointer=checkpointer)


def run_normal(question: str, thread_id: str = "t-1") -> dict[str, Any]:
    """正常跑完。"""
    app = build_graph_with_checkpoint()
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke({"question": question}, config=config)
    return {"result": result, "thread_id": thread_id}


def run_with_interrupt(question: str, thread_id: str = "t-int") -> dict[str, Any]:
    """在 generate 前暂停（人工审核），然后恢复。"""
    app = build_graph_with_checkpoint(interrupt_before="generate")
    config = {"configurable": {"thread_id": thread_id}}

    # 第一次 invoke：跑到 generate 前停下
    first = app.invoke({"question": question}, config=config)

    # 检查暂停点
    state_snapshot = app.get_state(config=config)
    paused_at = state_snapshot.next if hasattr(state_snapshot, "next") else None

    # 恢复执行
    resumed = app.invoke(None, config=config)

    return {
        "first_run_state": first,
        "paused_at": paused_at,
        "resumed": resumed,
        "first_steps": first.get("steps", []),
        "resumed_steps": resumed.get("steps", []),
    }


def run_same_thread_two_questions(q1: str, q2: str) -> dict[str, Any]:
    """同 thread_id 连续问两个问题。

    第二次 invoke 会从上一次的 final state 开始（merge），不是从头开始。
    这就是「记忆」—— 图记住了上次的状态。
    """
    app = build_graph_with_checkpoint()
    config = {"configurable": {"thread_id": "t-memory"}}

    r1 = app.invoke({"question": q1}, config=config)
    state_after_q1 = app.get_state(config=config)

    r2 = app.invoke({"question": q2}, config=config)

    return {
        "q1_result": r1,
        "q2_result": r2,
        "q1_answer": r1.get("answer"),
        "q2_answer": r2.get("answer"),
        "q2_inherits_q1_query": r2.get("query") != q2.replace("？", "").replace("?", "").strip() if q1 != q2 else True,
    }