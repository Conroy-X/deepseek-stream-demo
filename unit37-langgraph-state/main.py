"""Unit 37 · LangGraph State 实验台（端口 8037）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import graph as g

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Unit 37 · LangGraph State")


class AskIn(BaseModel):
    question: str


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 37, "framework": "langgraph", "state_fields": ["question", "query", "results", "answer", "rounds"]}


@app.post("/api/run")
def api_run(payload: AskIn) -> dict[str, Any]:
    return g.trace_execution(payload.question)


@app.get("/api/single")
def api_single(q: str = "MCP 是什么？") -> dict[str, Any]:
    app = g.build_single_node_graph()
    result = app.invoke({"question": q, "rounds": 0})
    return {"input": q, "output": result}


@app.get("/api/merge")
def api_merge() -> dict[str, Any]:
    """演示 merge vs 覆盖。"""
    app = g.build_overwrite_graph()
    result = app.invoke({"question": "原始问题", "rounds": 0})
    return {
        "input": {"question": "原始问题", "rounds": 0},
        "output": result,
        "question_overwritten": result["question"] == "被改写了",
        "answer_added": result["answer"] == "我补了答案",
        "rounds_final": result["rounds"],
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


# ── 自检 ─────────────────────────────────────────────────────────────────────

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    from fastapi.testclient import TestClient

    print("\n=== Unit 37 · LangGraph State 自检 ===\n")

    print("  —— State 定义与 TypedDict ——")
    # TypedDict 在运行时是 dict
    s: g.ResearchState = {"question": "test", "query": "", "results": [], "answer": "", "rounds": 0}
    check("State 运行时是 dict", isinstance(s, dict), str(type(s)))
    check("State 有 5 个字段", len(g.ResearchState.__annotations__) == 5, str(g.ResearchState.__annotations__))
    check("字段名正确", set(g.ResearchState.__annotations__) == {"question", "query", "results", "answer", "rounds"}, str(g.ResearchState.__annotations__))

    print("  —— 单节点图 ——")
    app1 = g.build_single_node_graph()
    r = app1.invoke({"question": "MCP 是什么？", "rounds": 0})
    check("单节点图能 invoke", isinstance(r, dict), str(type(r)))
    check("question 保留", r["question"] == "MCP 是什么？", str(r.get("question")))
    check("query 被改写", r["query"] == '"MCP 是什么"', str(r.get("query")))
    check("rounds 累加到 1", r["rounds"] == 1, str(r.get("rounds")))
    check("没设 answer（单节点不调 answer_node）", "answer" not in r or r.get("answer") == "", str(r.get("answer")))

    print("  —— 三节点图：rewrite → search → answer ——")
    app3 = g.build_three_node_graph()
    r = app3.invoke({"question": "MCP 是什么？", "rounds": 0})
    check("三节点图 invoke 成功", isinstance(r, dict), str(type(r)))
    check("query 被改写", r["query"] == '"MCP 是什么"', str(r.get("query")))
    check("results 有内容", len(r["results"]) >= 1, str(r.get("results")))
    check("MCP 搜索命中", any("MCP" in x for x in r["results"]), str(r.get("results")))
    check("answer 被生成", bool(r.get("answer")), str(r.get("answer")))
    check("rounds = 3（三个节点各加 1）", r["rounds"] == 3, str(r.get("rounds")))
    check("answer 含搜索结果", "MCP" in r["answer"], str(r.get("answer")))

    r2 = app3.invoke({"question": "RAG 怎么优化？", "rounds": 0})
    check("RAG 搜索命中", any("RAG" in x or "检索" in x for x in r2["results"]), str(r2.get("results")))
    check("RAG 答案含结果", "RAG" in r2["answer"] or "召回" in r2["answer"], str(r2.get("answer")))

    r3 = app3.invoke({"question": "无关问题", "rounds": 0})
    check("无关问题也有结果（不报错）", len(r3["results"]) >= 1, str(r3.get("results")))
    check("无关问题返回没有找到", "没有找到" in r3["results"][0], str(r3.get("results")))

    print("  —— merge 语义：返回 partial dict ——")
    app_noop = g.build_noop_graph()
    r = app_noop.invoke({"question": "保持不变", "rounds": 5})
    check("noop 不改任何字段", r["question"] == "保持不变" and r["rounds"] == 5, str(r))
    check("noop 返回的 state 和输入一致", r == {"question": "保持不变", "rounds": 5}, str(r))

    print("  —— 覆盖 vs merge ——")
    app_ow = g.build_overwrite_graph()
    r = app_ow.invoke({"question": "原始问题", "rounds": 0})
    check("set_a 覆盖了 question", r["question"] == "被改写了", str(r.get("question")))
    check("keep_b 没改 question（保留 set_a 的值）", r["question"] == "被改写了", str(r.get("question")))
    check("keep_b 补了 answer", r["answer"] == "我补了答案", str(r.get("answer")))
    check("rounds 被 keep_b 覆盖为 2", r["rounds"] == 2, str(r.get("rounds")))
    check("不是 deep merge（top-level key 覆盖）", r["rounds"] == 2 and not r["rounds"] == 1, str(r))

    print("  —— 对比手写循环（Unit 34 模式）——")
    trace = g.trace_execution("MCP 是什么？")
    check("手写循环也能跑", isinstance(trace["handwritten"], dict), str(trace))
    check("两者答案一致", trace["same_answer"] is True, str(trace))
    check("两者轮次一致", trace["same_rounds"] is True, str(trace))
    check("手写循环 rounds = 3", trace["handwritten"]["rounds"] == 3, str(trace["handwritten"]))
    check("LangGraph rounds = 3", trace["final"]["rounds"] == 3, str(trace["final"]))

    print("  —— 没传 rounds 时默认行为 ——")
    r = app3.invoke({"question": "没传 rounds"})
    check("没传 rounds 不报错", isinstance(r, dict), str(type(r)))
    check("rounds 被节点设为 3", r["rounds"] == 3, str(r.get("rounds")))

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    h = client.get("/health").json()
    check("health 报 5 个 state 字段", len(h["state_fields"]) == 5, str(h))

    r = client.post("/api/run", json={"question": "MCP 是什么？"})
    check("/api/run 跑通", r.status_code == 200 and "final" in r.json(), r.text[:120])
    check("/api/run 两者答案一致", r.json()["same_answer"] is True, str(r.json()))

    r = client.get("/api/single", params={"q": "test"})
    check("/api/single 跑通", r.status_code == 200 and r.json()["output"]["query"] == '"test"', r.text[:120])

    r = client.get("/api/merge")
    check("/api/merge 跑通", r.status_code == 200 and r.json()["question_overwritten"] is True, r.text[:120])

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200)
    check("页面含关键文案", "LangGraph" in page.text and len(page.text) > 3000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()