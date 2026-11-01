"""Unit 40 · Checkpoint / Memory 实验台（端口 8040）。"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
import graph as g

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Unit 40 · Checkpoint / Memory")

class AskIn(BaseModel):
    question: str
    thread_id: str = "t-1"

@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 40, "features": ["checkpoint", "thread_id", "interrupt_before"]}

@app.post("/api/run")
def api_run(payload: AskIn) -> dict[str, Any]:
    return g.run_normal(payload.question, payload.thread_id)

@app.post("/api/interrupt")
def api_interrupt(payload: AskIn) -> dict[str, Any]:
    return g.run_with_interrupt(payload.question)

@app.post("/api/memory")
def api_memory(payload: AskIn) -> dict[str, Any]:
    return g.run_same_thread_two_questions(payload.question, "第二个问题？")

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
    print("\n=== Unit 40 · Checkpoint / Memory 自检 ===\n")

    print("  —— 带 checkpoint 正常跑 ——")
    r = g.run_normal("MCP 是什么？", "t-1")
    check("checkpoint 跑通", isinstance(r["result"], dict), str(r))
    check("有 3 步", r["result"].get("steps") == ["rewrite", "search", "generate"], str(r["result"].get("steps")))
    check("有答案", bool(r["result"].get("answer")), str(r["result"].get("answer")))
    check("有 thread_id", r["thread_id"] == "t-1", str(r))

    print("  —— 不同 thread 独立 ——")
    r1 = g.run_normal("问题一", "t-a")
    r2 = g.run_normal("问题二", "t-b")
    check("不同 thread 答案不同", r1["result"]["answer"] != r2["result"]["answer"], f"{r1['result']['answer']} vs {r2['result']['answer']}")
    check("t-a 答案含问题一", "问题一" in r1["result"]["answer"], str(r1["result"]["answer"]))
    check("t-b 答案含问题二", "问题二" in r2["result"]["answer"], str(r2["result"]["answer"]))

    print("  —— interrupt_before 人工暂停 ——")
    intr = g.run_with_interrupt("MCP 是什么？", "t-int")
    check("第一次跑停在 generate 前", "generate" not in intr["first_steps"], str(intr["first_steps"]))
    check("第一次跑了 rewrite + search", intr["first_steps"] == ["rewrite", "search"], str(intr["first_steps"]))
    check("暂停点包含 generate", intr["paused_at"] is not None, str(intr["paused_at"]))
    check("恢复后跑了 generate", "generate" in intr["resumed_steps"], str(intr["resumed_steps"]))
    check("恢复后答案被生成", bool(intr["resumed"].get("answer")), str(intr["resumed"].get("answer")))
    check("恢复后步骤完整（4 步）", intr["resumed_steps"] == ["rewrite", "search", "generate"], str(intr["resumed_steps"]))

    print("  —— 同 thread 连续问（记忆）——")
    mem = g.run_same_thread_two_questions("第一个问题", "第二个问题")
    check("第一次有答案", bool(mem["q1_answer"]), str(mem["q1_answer"]))
    check("第二次有答案", bool(mem["q2_answer"]), str(mem["q2_answer"]))
    check("两次答案不同", mem["q1_answer"] != mem["q2_answer"], f"{mem['q1_answer']} vs {mem['q2_answer']}")

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    r = client.post("/api/run", json={"question": "test", "thread_id": "t-http"})
    check("/api/run 跑通", r.status_code == 200 and "result" in r.json(), r.text[:120])
    r = client.post("/api/interrupt", json={"question": "test"})
    check("/api/interrupt 跑通", r.status_code == 200 and "resumed" in r.json(), r.text[:120])
    r = client.post("/api/memory", json={"question": "第一个问题"})
    check("/api/memory 跑通", r.status_code == 200 and "q1_result" in r.json(), r.text[:120])
    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200)
    check("页面含关键文案", "Checkpoint" in page.text and len(page.text) > 2000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()