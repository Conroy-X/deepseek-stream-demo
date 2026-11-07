"""Unit 82 · LangGraph 整合：迷你图执行（节点/边/条件边）。

核心认知：Unit 37-40 学了 LangGraph 的概念，本单元做整合——
用一个迷你图执行器把 StateGraph 的核心语义（节点、边、条件路由、状态合并）跑通。
真实项目用 langgraph 包（Unit 45 已装），这里是「看懂图执行」的最小实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class MiniGraph:
    nodes: dict = field(default_factory=dict)        # name -> fn(state) -> state
    edges: list = field(default_factory=list)         # (from, to)
    conditional: dict = field(default_factory=dict)   # from -> fn(state) -> next_node
    _traces: list = field(default_factory=list)

    def add_node(self, name: str, fn: Callable) -> None:
        self.nodes[name] = fn

    def add_edge(self, src: str, dst: str) -> None:
        self.edges.append((src, dst))

    def add_conditional(self, src: str, router: Callable) -> None:
        self.conditional[src] = router

    def run(self, state: dict, start: str, end: str, max_steps: int = 10) -> dict:
        node = start
        for _ in range(max_steps):
            self._traces.append(node)
            state = self.nodes[node](state)
            if node == end:
                return {"state": state, "trace": self._traces}
            if node in self.conditional:
                node = self.conditional[node](state)
            else:
                nxt = [dst for src, dst in self.edges if src == node]
                if not nxt:
                    break
                node = nxt[0]
        return {"state": state, "trace": self._traces}


def run_selfcheck() -> int:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 82 · LangGraph 整合 自检\n")
    g = MiniGraph()
    g.add_node("start", lambda s: {**s, "x": 1})
    g.add_node("maybe", lambda s: {**s, "visited": s.get("visited", 0) + 1})
    g.add_node("end", lambda s: {**s, "done": True})
    g.add_edge("start", "maybe")
    g.add_conditional("maybe", lambda s: "end" if s["x"] == 1 else "start")
    r = g.run({}, "start", "end")
    check("图执行到达 end", r["state"].get("done") is True)
    check("条件路由生效", "maybe" in r["trace"] and "end" in r["trace"])
    check("执行轨迹有序", r["trace"][0] == "start" and r["trace"][-1] == "end")

    # 条件路由分支：x=0 时回 start（演示循环守卫）
    g2 = MiniGraph()
    g2.add_node("start", lambda s: {**s, "x": s.get("x", 0) + 1})
    g2.add_node("end", lambda s: s)
    g2.add_conditional("start", lambda s: "end" if s["x"] >= 2 else "start")
    r2 = g2.run({}, "start", "end", max_steps=10)
    check("循环守卫后收敛", r2["state"]["x"] == 2)

    # 最大步数防死循环
    g3 = MiniGraph()
    g3.add_node("loop", lambda s: s)
    g3.add_conditional("loop", lambda s: "loop")  # 永远回自己
    r3 = g3.run({}, "loop", "end", max_steps=5)
    check("max_steps 防死循环", len(r3["trace"]) == 5)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
