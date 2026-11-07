"""Unit 82 · LangGraph 优化：子图 / 并行节点 / 流式事件。

计划主题：把 Unit 37–40 的 LangGraph 概念推进到工程可用级别，三块：

  1. 子图（subgraph）：把一组节点封成一个可复用的图，父图把子图当一个节点调
  2. 并行节点（parallel）：同一层多个节点同时跑，结果合并
  3. 流式事件（streaming）：执行过程中逐步吐出事件，而不是等全部跑完

启动：uv run uvicorn main:app --port 8082
自检：uv run python main.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import FastAPI

Node = Callable[[dict], dict]


@dataclass
class Event:
    """流式事件：node_start / node_end / edge。"""
    kind: str
    node: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "node": self.node, "detail": self.detail}


@dataclass
class SubGraph:
    """子图：一个独立的图，可被父图当节点调用。"""
    name: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    entry: str = ""
    exit: str = ""

    def add_node(self, name: str, fn: Node) -> "SubGraph":
        self.nodes[name] = fn
        if not self.entry:
            self.entry = name
        return self

    def add_edge(self, src: str, dst: str) -> "SubGraph":
        self.edges.append((src, dst))
        return self

    def run(self, state: dict) -> dict:
        """顺序执行子图（子图内部暂不并行）。"""
        node = self.entry
        seen = set()
        while node and node not in seen:
            seen.add(node)
            state = self.nodes[node](state)
            nxt = [dst for src, dst in self.edges if src == node]
            node = nxt[0] if nxt else ""
        return state


@dataclass
class Graph:
    """父图：支持普通节点、子图节点、并行组。"""
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    parallel_groups: dict[str, list[str]] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)

    def add_node(self, name: str, fn: Node) -> "Graph":
        self.nodes[name] = fn
        return self

    def add_subgraph(self, name: str, sg: SubGraph) -> "Graph":
        """把子图包装成一个节点。"""
        self.nodes[name] = sg.run
        return self

    def add_parallel(self, group_name: str, members: list[str]) -> "Graph":
        """声明一组并行节点：group_name 是一个虚拟节点，跑完所有 members 再合并。"""
        self.parallel_groups[group_name] = members
        return self

    def add_edge(self, src: str, dst: str) -> "Graph":
        self.edges.append((src, dst))
        return self

    def _run_node(self, name: str, state: dict) -> dict:
        self.events.append(Event("node_start", name))
        out = self.nodes[name](state)
        self.events.append(Event("node_end", name))
        return out

    def run(self, state: dict, start: str, end: str, max_steps: int = 20) -> dict:
        node = start
        for _ in range(max_steps):
            if node in self.parallel_groups:
                # 并行组：所有成员基于同一份 state 跑，然后合并
                members = self.parallel_groups[node]
                self.events.append(Event("node_start", node, f"parallel x{len(members)}"))
                merged = dict(state)
                for m in members:
                    part = self._run_node(m, state)
                    merged.update(part)
                self.events.append(Event("node_end", node, "parallel merged"))
                state = merged
            else:
                if node not in self.nodes:
                    break
                state = self._run_node(node, state)
                if node == end:
                    break
            nxt = [dst for src, dst in self.edges if src == node]
            if not nxt:
                break
            node = nxt[0]
        return state

    def stream(self, state: dict, start: str, end: str, max_steps: int = 20) -> list[dict]:
        """执行并返回完整事件流（真实 LangGraph 是异步 yield）。"""
        self.events = []
        self.run(state, start, end, max_steps)
        return [e.as_dict() for e in self.events]


app = FastAPI(title="Unit 82 · LangGraph 优化", version="0.1.0")


def build_demo() -> Graph:
    """构建一个演示图：预处理 → 并行（检索+翻译）→ 子图（生成）→ 结束。"""
    g = Graph()

    g.add_node("pre", lambda s: {**s, "q": s["raw"].strip()})
    g.add_node("search", lambda s: {**s, "docs": [f"关于{s['q']}的资料"]})
    g.add_node("translate", lambda s: {**s, "en": f"translated:{s['q']}"})

    # 子图：生成 + 引用
    sg = SubGraph(name="generate")
    sg.add_node("draft", lambda s: {**s, "draft": f"回答{s['q']}"})
    sg.add_node("cite", lambda s: {**s, "answer": s["draft"] + " [1]"})
    sg.add_edge("draft", "cite")
    g.add_subgraph("gen", sg)

    g.add_parallel("par", ["search", "translate"])
    g.add_edge("pre", "par")
    g.add_edge("par", "gen")
    return g


@app.get("/run", summary="跑一遍图，返回最终 state")
async def run_graph(q: str = "年假") -> dict:
    g = build_demo()
    state = g.run({"raw": q}, "pre", "gen")
    return {"state": state, "events": len(g.events)}


@app.get("/stream", summary="流式事件：逐步返回 node_start/node_end")
async def stream_graph(q: str = "年假") -> dict:
    g = build_demo()
    events = g.stream({"raw": q}, "pre", "gen")
    return {"events": events, "count": len(events)}


@app.get("/", include_in_schema=False)
async def index() -> dict:
    return {"hint": "/run?q=年假 看结果；/stream?q=年假 看事件流"}


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

    print("Unit 82 · LangGraph 优化 自检\n")

    # ---- 子图 ----
    sg = SubGraph(name="t")
    sg.add_node("a", lambda s: {**s, "x": s.get("x", 0) + 1})
    sg.add_node("b", lambda s: {**s, "y": s.get("y", 0) + 10})
    sg.add_edge("a", "b")
    out = sg.run({})
    check("子图能跑", out["x"] == 1 and out["y"] == 10, str(out))
    check("子图 entry 自动设为第一个节点", sg.entry == "a")

    # ---- 父图调子图 ----
    g = Graph()
    g.add_node("pre", lambda s: {**s, "ok": True})
    g.add_subgraph("sub", sg)
    g.add_edge("pre", "sub")
    state = g.run({}, "pre", "sub")
    check("父图能调子图", state["ok"] is True and state["x"] == 1)

    # ---- 并行节点 ----
    g2 = Graph()
    g2.add_node("a", lambda s: {**s, "a_done": True})
    g2.add_node("b", lambda s: {**s, "b_done": True})
    g2.add_node("c", lambda s: {**s, "c_done": True})
    g2.add_parallel("par", ["a", "b"])
    g2.add_edge("par", "c")
    st = g2.run({}, "par", "c")
    check("并行组两个成员都跑了", st.get("a_done") and st.get("b_done"), str(st))
    check("并行后能继续下一个节点", st.get("c_done") is True)
    check("并行事件有 parallel 标记", any("parallel" in e.detail for e in g2.events))

    # ---- 流式事件 ----
    events = g2.stream({}, "par", "c")
    kinds = [e["kind"] for e in events]
    check("流式有 node_start", "node_start" in kinds)
    check("流式有 node_end", "node_end" in kinds)
    check("事件数 > 0", len(events) > 0)
    check("事件有序（start 在 end 前）", kinds.index("node_start") < kinds.index("node_end"))

    # ---- 防死循环 ----
    g3 = Graph()
    g3.add_node("loop", lambda s: s)
    g3.add_edge("loop", "loop")
    st3 = g3.run({}, "loop", "never", max_steps=5)
    check("max_steps 防死循环", len(g3.events) <= 10, str(len(g3.events)))

    # ---- HTTP ----
    c = TestClient(app)
    r = c.get("/run", params={"q": "年假"})
    check("HTTP /run 200", r.status_code == 200)
    body = r.json()
    check("HTTP 返回 state", "state" in body)
    check("最终答案带引用", "[1]" in body["state"]["answer"], body["state"].get("answer", ""))
    check("并行结果都在", "docs" in body["state"] and "en" in body["state"])

    r = c.get("/stream", params={"q": "年假"})
    check("HTTP /stream 200", r.status_code == 200)
    sb = r.json()
    check("HTTP 事件流非空", sb["count"] > 0)
    check("HTTP 事件含子图节点", any(e["node"] == "gen" for e in sb["events"]), str(sb["events"][:5]))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
