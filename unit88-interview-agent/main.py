"""Unit 88 · 面试 Agent+MCP：题库 + 自测。"""

from __future__ import annotations

BANK = [
    {"topic": "agent", "q": "Agent 和普通 LLM 调用有什么区别？", "points": ["循环", "工具", "规划"]},
    {"topic": "tool", "q": "Function Calling 的流程？", "points": ["声明", "模型返回调用", "执行", "回填"]},
    {"topic": "loop", "q": "Agent 死循环怎么办？", "points": ["步数预算", "去重", "终止条件"]},
    {"topic": "mcp", "q": "MCP 解决什么问题？", "points": ["标准化", "工具发现", "复用"]},
    {"topic": "hitl", "q": "什么操作必须 HITL？", "points": ["危险操作", "审批", "责任"]},
    {"topic": "memory", "q": "Agent 的记忆分几种？", "points": ["短期", "长期", "工作记忆"]},
    {"topic": "langgraph", "q": "LangGraph 的核心概念？", "points": ["状态", "节点", "边"]},
    {"topic": "permission", "q": "工具权限怎么管？", "points": ["最小权限", "RBAC", "审批"]},
]


def check_bank(bank: list[dict]) -> dict:
    topics = [b["topic"] for b in bank]
    return {"count": len(bank), "topics": topics,
            "all_have_points": all(len(b.get("points", [])) >= 3 for b in bank),
            "no_dup": len(topics) == len(set(topics))}


def self_quiz(bank: list[dict], answers: dict[str, str]) -> dict:
    return {b["topic"]: sum(1 for p in b["points"] if p in answers.get(b["topic"], "")) / len(b["points"])
            for b in bank}


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

    print("Unit 88 · 面试 Agent+MCP 自检\n")
    r = check_bank(BANK)
    check("题库 8 题", r["count"] == 8)
    check("每题至少 3 个要点", r["all_have_points"])
    check("话题不重复", r["no_dup"])
    check("覆盖 mcp/agent/langgraph", all(t in r["topics"] for t in ["mcp", "agent", "langgraph"]))
    good = {b["topic"]: " ".join(b["points"]) for b in BANK}
    check("满分答案全对", all(s == 1.0 for s in self_quiz(BANK, good).values()))
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
