"""Unit 39 · Conditional Edge 实验台（端口 8039）。"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
import graph as g

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Unit 39 · Conditional Edge")

class AskIn(BaseModel):
    question: str

@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 39, "routes": ["simple", "complex", "unknown"]}

@app.post("/api/run")
def api_run(payload: AskIn) -> dict[str, Any]:
    return g.run_question(payload.question)

@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")

passed = failed = 0
def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok: passed += 1; print(f"  [PASS] {name}")
    else: failed += 1; print(f"  [FAIL] {name}  {detail}")

def main() -> None:
    from fastapi.testclient import TestClient
    print("\n=== Unit 39 · Conditional Edge 自检 ===\n")

    print("  —— 条件路由 ——")
    app_g = g.build_conditional_graph()

    r = app_g.invoke({"question": "你好"})
    check("简单问题走 simple", r["question_type"] == "simple", str(r.get("question_type")))
    check("simple 路径 2 步", r["steps"] == ["classify", "simple"], str(r.get("steps")))
    check("simple 不走搜索", "search" not in r.get("steps", []), str(r.get("steps")))
    check("simple 有答案", "简单回答" in r.get("answer", ""), str(r.get("answer")))

    r = app_g.invoke({"question": "MCP 是什么？"})
    check("复杂问题走 complex", r["question_type"] == "complex", str(r.get("question_type")))
    check("complex 路径 4 步", r["steps"] == ["classify", "search", "generate"], str(r.get("steps")))
    check("complex 走搜索", "search" in r.get("steps", []), str(r.get("steps")))
    check("complex 有搜索结果", len(r.get("results", [])) >= 1, str(r.get("results")))
    check("complex 答案含 MCP", "MCP" in r.get("answer", ""), str(r.get("answer")))

    r = app_g.invoke({"question": "adasdjqwerq"})
    check("未知问题走 unknown", r["question_type"] == "unknown", str(r.get("question_type")))
    check("unknown 走 fallback", r["steps"] == ["classify", "fallback"], str(r.get("steps")))
    check("fallback 答案是固定话术", "不知道" in r.get("answer", ""), str(r.get("answer")))

    print("  —— 路由函数 ——")
    from graph import router_fn
    check("router 返回 simple", router_fn({"question_type": "simple"}) == "simple", str(router_fn({"question_type": "simple"})))
    check("router 返回 complex", router_fn({"question_type": "complex"}) == "complex", str(router_fn({"question_type": "complex"})))
    check("router 兜底 unknown", router_fn({}) == "unknown", str(router_fn({})))

    print("  —— 对比线性图（无条件路由）——")
    linear = g.build_linear_graph()
    r_lin = linear.invoke({"question": "你好"})
    check("线性图简单问题也走搜索", "search" in r_lin.get("steps", []), str(r_lin.get("steps")))
    check("线性图步骤更多", len(r_lin.get("steps", [])) > len(app_g.invoke({"question": "你好"}).get("steps", [])), str(r_lin.get("steps")))

    print("  —— 多种复杂问题 ——")
    for q in ["MCP 是什么？", "RAG 怎么优化？", "LangGraph 是什么"]:
        r = app_g.invoke({"question": q})
        check(f"「{q}」走 complex", r["question_type"] == "complex", str(r.get("question_type")))
        check(f"「{q}」有答案", bool(r.get("answer")), str(r.get("answer", "")[:40]))

    print("  —— 空问题 ——")
    r = app_g.invoke({"question": ""})
    check("空问题走 unknown", r["question_type"] == "unknown", str(r.get("question_type")))

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    r = client.post("/api/run", json={"question": "MCP 是什么？"})
    check("/api/run complex 跑通", r.status_code == 200 and r.json()["question_type"] == "complex", r.text[:120])
    r = client.post("/api/run", json={"question": "你好"})
    check("/api/run simple 跑通", r.status_code == 200 and r.json()["question_type"] == "simple", r.text[:120])
    r = client.post("/api/run", json={"question": "zxcvbnmasdf"})
    check("/api/run unknown 跑通", r.status_code == 200 and r.json()["question_type"] == "unknown", r.text[:120])
    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200)
    check("页面含关键文案", "Conditional" in page.text and len(page.text) > 2000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()