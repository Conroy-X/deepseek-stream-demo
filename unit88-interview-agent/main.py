"""Unit 88 · 面试 Agent+MCP：30 题题库 + 自测打分。

计划要求：重点考工具设计、状态机、错误恢复。
"""

from __future__ import annotations

from fastapi import FastAPI

BANK = [
    # ---- Agent 基础（1-8）----
    {"topic": "agent", "q": "Agent 和普通 LLM 调用有什么区别？", "points": ["循环", "工具", "规划"]},
    {"topic": "function-calling", "q": "Function Calling 的完整消息流转？", "points": ["声明", "tool_calls", "执行", "回填"]},
    {"topic": "loop", "q": "Agent 死循环怎么办？", "points": ["步数预算", "去重", "终止条件"]},
    {"topic": "planning", "q": "ReAct 和 Plan-and-Execute 的区别？", "points": ["交替", "先规划", "适用"]},
    {"topic": "tool-design", "q": "工具怎么设计才好用？", "points": ["单一职责", "描述清晰", "参数简单"]},
    {"topic": "tool-choice", "q": "模型选错工具怎么办？", "points": ["描述", "few-shot", "减少工具数"]},
    {"topic": "state", "q": "Agent 的状态怎么管理？", "points": ["结构化", "持久化", "可恢复"]},
    {"topic": "termination", "q": "Agent 什么时候该停？", "points": ["目标达成", "预算耗尽", "不可恢复"]},
    # ---- 工具与权限（9-14）----
    {"topic": "permission", "q": "工具权限怎么管？", "points": ["最小权限", "RBAC", "审批"]},
    {"topic": "idempotent", "q": "工具为什么要幂等？", "points": ["重试", "重复执行", "幂等键"]},
    {"topic": "tool-result", "q": "工具返回结果太大怎么办？", "points": ["截断", "摘要", "分页"]},
    {"topic": "tool-error", "q": "工具报错 Agent 怎么处理？", "points": ["结构化错误", "重试", "换路"]},
    {"topic": "sandbox", "q": "执行外部代码怎么保证安全？", "points": ["沙箱", "资源限制", "超时"]},
    {"topic": "audit", "q": "Agent 的调用怎么审计？", "points": ["留痕", "参数", "可回放"]},
    # ---- MCP（15-22）----
    {"topic": "mcp", "q": "MCP 解决什么问题？", "points": ["标准化", "工具发现", "复用"]},
    {"topic": "mcp-arch", "q": "MCP 的 Host/Client/Server 分别是什么？", "points": ["Host 宿主", "Client 连接", "Server 提供"]},
    {"topic": "mcp-primitives", "q": "MCP 的 Tools/Resources/Prompts 区别？", "points": ["Tools 执行", "Resources 读取", "Prompts 模板"]},
    {"topic": "mcp-vs-api", "q": "MCP 和 REST API 的区别？", "points": ["自描述", "运行时发现", "面向模型"]},
    {"topic": "mcp-lifecycle", "q": "MCP 的连接生命周期？", "points": ["initialize", "能力协商", "关闭"]},
    {"topic": "mcp-transport", "q": "MCP 的传输方式有哪些？", "points": ["stdio", "SSE", "HTTP"]},
    {"topic": "mcp-security", "q": "MCP 的安全风险？", "points": ["工具投毒", "权限", "数据外泄"]},
    {"topic": "mcp-multi", "q": "多 MCP server 怎么管理？", "points": ["工具命名空间", "优先级", "隔离"]},
    # ---- 编排与记忆（23-27）----
    {"topic": "langgraph", "q": "LangGraph 的核心概念？", "points": ["状态", "节点", "边"]},
    {"topic": "subgraph", "q": "子图有什么用？", "points": ["复用", "隔离", "组合"]},
    {"topic": "parallel", "q": "并行节点怎么合并结果？", "points": ["reducer", "冲突", "顺序"]},
    {"topic": "memory", "q": "Agent 的记忆分几种？", "points": ["短期", "长期", "工作记忆"]},
    {"topic": "hitl", "q": "什么操作必须 HITL？", "points": ["危险操作", "审批", "责任"]},
    # ---- 优化（28-30）----
    {"topic": "cost-opt", "q": "Agent 成本怎么降？", "points": ["小模型", "缓存", "减少步数"]},
    {"topic": "latency", "q": "Agent 延迟怎么降？", "points": ["并行", "流式", "预热"]},
    {"topic": "eval-agent", "q": "Agent 怎么评测？", "points": ["成功率", "工具正确", "步数"]},
]

REQUIRED_TOPICS = ["agent", "function-calling", "loop", "mcp", "langgraph", "hitl", "eval-agent"]


def check_bank(bank: list[dict]) -> dict:
    topics = [b["topic"] for b in bank]
    return {
        "count": len(bank),
        "topics": topics,
        "all_have_points": all(len(b.get("points", [])) >= 3 for b in bank),
        "no_dup_topic": len(topics) == len(set(topics)),
        "no_dup_question": len({b["q"] for b in bank}) == len(bank),
        "covered": all(t in topics for t in REQUIRED_TOPICS),
    }


def self_quiz(bank: list[dict], answers: dict[str, str], pass_line: float = 0.6) -> dict:
    scores = {}
    for b in bank:
        ans = answers.get(b["topic"], "")
        hit = sum(1 for p in b["points"] if p in ans)
        scores[b["topic"]] = round(hit / len(b["points"]), 2)
    weak = [t for t, s in scores.items() if s < pass_line]
    avg = sum(scores.values()) / len(scores) if scores else 0.0
    return {"scores": scores, "weak": weak, "avg": round(avg, 2), "passed": len(weak) == 0}


app = FastAPI(title="Unit 88 · 面试 Agent+MCP", version="0.1.0")


@app.get("/bank", summary="30 题题库")
async def bank(topic: str | None = None) -> dict:
    items = [b for b in BANK if topic is None or b["topic"] == topic]
    return {"count": len(items), "items": items}


@app.get("/quiz/check", summary="题库自检")
async def quiz_check() -> dict:
    return check_bank(BANK)


@app.get("/", include_in_schema=False)
async def index() -> dict:
    return {"hint": "/bank 看题库；/bank?topic=mcp 过滤；/quiz/check 题库自检"}


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

    print("Unit 88 · 面试 Agent+MCP（30 题）自检\n")
    r = check_bank(BANK)
    check("题库 30 题", r["count"] == 30, str(r["count"]))
    check("每题至少 3 个要点", r["all_have_points"])
    check("话题不重复", r["no_dup_topic"])
    check("题目不重复", r["no_dup_question"])
    check("覆盖核心话题", r["covered"])
    check("MCP 题目 >= 8", sum(1 for t in r["topics"] if t.startswith("mcp")) >= 8)

    good = {b["topic"]: " ".join(b["points"]) for b in BANK}
    check("满分全部通过", self_quiz(BANK, good)["passed"] is True)
    check("空答案全不及格", len(self_quiz(BANK, {})["weak"]) == 30)

    c = TestClient(app)
    r1 = c.get("/bank")
    check("HTTP /bank 200", r1.status_code == 200)
    check("HTTP 30 题", r1.json()["count"] == 30)
    r2 = c.get("/bank", params={"topic": "mcp"})
    check("HTTP 按话题过滤", all(i["topic"].startswith("mcp") for i in r2.json()["items"]))
    check("HTTP /quiz/check", c.get("/quiz/check").json()["count"] == 30)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())