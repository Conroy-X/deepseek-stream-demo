"""Unit 81 · Agent 优化：步数预算 + 工具去重。

核心认知：Agent 跑飞的两个典型问题：
  1. 无限循环 —— 用**步数预算**（max_steps）强制终止
  2. 重复调同一个工具 —— 用**工具去重**（同一参数只调一次）
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentLoop:
    max_steps: int = 5
    tools: dict = field(default_factory=lambda: {"search": lambda x: f"结果:{x}"})
    _steps: int = 0
    _called: set = field(default_factory=set)

    def run(self, goal: str, plan: list[tuple[str, str]]) -> dict:
        """plan 是 [(tool, arg), ...]，返回执行报告。"""
        results = []
        for tool, arg in plan:
            if self._steps >= self.max_steps:
                return {"status": "budget_exceeded", "steps": self._steps, "results": results}
            key = (tool, arg)
            if key in self._called:
                results.append({"tool": tool, "arg": arg, "deduped": True})
                continue
            self._called.add(key)
            self._steps += 1
            results.append({"tool": tool, "arg": arg, "output": self.tools[tool](arg)})
        return {"status": "ok", "steps": self._steps, "results": results}


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

    print("Unit 81 · Agent 优化 自检\n")
    loop = AgentLoop(max_steps=3)
    r = loop.run("g", [("search", "a"), ("search", "b"), ("search", "a")])
    check("步数预算内完成", r["status"] == "ok" and r["steps"] == 2, str(r["steps"]))
    outputs = [x for x in r["results"] if x.get("output")]
    check("重复调用被去重", len(outputs) == 2 and any(x.get("deduped") for x in r["results"]))

    # 步数预算强制终止
    loop2 = AgentLoop(max_steps=2)
    r2 = loop2.run("g", [("search", "a"), ("search", "b"), ("search", "c"), ("search", "d")])
    check("超预算终止", r2["status"] == "budget_exceeded" and r2["steps"] == 2)
    check("预算外不再执行", len(r2["results"]) <= 2)

    # 空计划
    r3 = AgentLoop().run("g", [])
    check("空计划 ok", r3["status"] == "ok" and r3["steps"] == 0)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
