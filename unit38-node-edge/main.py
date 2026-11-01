"""Unit 38 · Node / Edge 实验台（端口 8038）。"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
import graph as g

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Unit 38 · Node / Edge")

class AskIn(BaseModel):
    question: str

@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 38, "nodes": ["rewrite", "search", "rerank", "generate", "cite"]}

@app.post("/api/run")
def api_run(payload: AskIn) -> dict[str, Any]:
    return g.run_with_stream(payload.question)

@app.get("/api/compare")
def api_compare(q: str = "MCP 是什么？") -> dict[str, Any]:
    full = g.build_graph().invoke({"question": q})
    skip = g.build_skip_rerank_graph().invoke({"question": q})
    return {"full_steps": full.get("steps", []), "skip_steps": skip.get("steps", []),
            "full_cited": full.get("cited_answer"), "skip_cited": skip.get("cited_answer"),
            "full_has_rerank": "rerank" in full.get("steps", []),
            "skip_no_rerank": "rerank" not in skip.get("steps", [])}

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
    print("\n=== Unit 38 · Node / Edge 自检 ===\n")

    print("  —— 五节点图 ——")
    app_g = g.build_graph()
    r = app_g.invoke({"question": "MCP 是什么？"})
    check("invoke 成功", isinstance(r, dict), str(type(r)))
    check("5 个节点都执行了", len(r.get("steps", [])) == 5, str(r.get("steps")))
    check("steps 顺序正确", r["steps"] == ["rewrite", "search", "rerank", "generate", "cite"], str(r.get("steps")))
    check("raw_results 有内容", len(r.get("raw_results", [])) >= 1, str(r.get("raw_results")))
    check("ranked_results 有内容", len(r.get("ranked_results", [])) >= 1, str(r.get("ranked_results")))
    check("answer 被生成", bool(r.get("answer")), str(r.get("answer")))
    check("cited_answer 含引用标注", "[" in r.get("cited_answer", ""), str(r.get("cited_answer")))
    check("cited_answer 在 answer 之后", r.get("cited_answer", "").startswith(r.get("answer", "")[:5]), str(r.get("cited_answer")))

    print("  —— rerank 排序 ——")
    r_mcp = app_g.invoke({"question": "MCP 是什么？"})
    ranked = r_mcp.get("ranked_results", [])
    check("rerank 按 snippet 长度排序", len(ranked) >= 2 and len(ranked[0]["snippet"]) >= len(ranked[-1]["snippet"]), str(ranked))

    print("  —— stream 模式 ——")
    trace = g.run_with_stream("MCP 是什么？")
    check("stream 返回 5 步", trace["step_count"] == 5, str(trace["step_count"]))
    check("stream 每步有 node 名", all("node" in s for s in trace["steps"]), str(trace["steps"][0]))
    check("stream 第一步是 rewrite", trace["steps"][0]["node"] == "rewrite", str(trace["steps"][0]))
    check("stream 最后一步是 cite", trace["steps"][-1]["node"] == "cite", str(trace["steps"][-1]))
    check("stream 最终结果和 invoke 一致", trace["final"]["cited_answer"] == r_mcp["cited_answer"], str(trace["final"]))

    print("  —— 跳过 rerank 对比 ——")
    skip_app = g.build_skip_rerank_graph()
    r_skip = skip_app.invoke({"question": "MCP 是什么？"})
    check("跳过版只有 4 步", len(r_skip.get("steps", [])) == 4, str(r_skip.get("steps")))
    check("跳过版不含 rerank", "rerank" not in r_skip.get("steps", []), str(r_skip.get("steps")))
    check("跳过版 ranked_results 为空（没跑 rerank）", not r_skip.get("ranked_results"), str(r_skip.get("ranked_results")))
    check("跳过版仍能用 raw_results 生成答案", bool(r_skip.get("answer")), str(r_skip.get("answer")))

    print("  —— 不同问题路由 ——")
    for q in ["MCP 是什么？", "RAG 怎么优化？", "无关问题"]:
        r = app_g.invoke({"question": q})
        check(f"「{q}」跑通", isinstance(r, dict) and bool(r.get("cited_answer")), str(r.get("cited_answer", "")[:40]))

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    h = client.get("/health").json()
    check("health 报 5 个节点", len(h["nodes"]) == 5, str(h))
    r = client.post("/api/run", json={"question": "MCP 是什么？"})
    check("/api/run 跑通", r.status_code == 200 and r.json()["step_count"] == 5, r.text[:120])
    r = client.get("/api/compare", params={"q": "MCP 是什么？"})
    check("/api/compare 跑通", r.status_code == 200 and r.json()["full_has_rerank"] is True, r.text[:120])
    check("/api/compare skip 无 rerank", r.json()["skip_no_rerank"] is True, str(r.json()))
    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200)
    check("页面含关键文案", "Node" in page.text and len(page.text) > 2000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()