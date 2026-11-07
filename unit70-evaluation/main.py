"""Unit 70 · Evaluation —— FastAPI 入口：离线评测报告。

启动：
    uv run uvicorn main:app --reload --port 8070

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from eval import AgentEval, AgentRun, evaluate_refusal, judge_answer, score_keywords

app = FastAPI(title="Unit 70 · Evaluation", version="0.1.0")


@app.get("/eval/rag", summary="RAG 答案评测示例：正确性 + 拒答混淆矩阵")
async def eval_rag() -> dict:
    # 示例：金标准 + 预测
    samples = [
        {"q": "退款多久到账", "gold_kw": ["退款", "3-5"], "pred": "退款原路退回，3-5 个工作日到账 [1]"},
        {"q": "年假怎么申请", "gold_kw": ["提前", "系统"], "pred": "在系统提交申请 [1]"},
    ]
    scores = [score_keywords(s["pred"], s["gold_kw"]) for s in samples]
    refusal = evaluate_refusal([(False, False), (True, True), (False, True)]).as_dict()
    return {"answer_scores": scores, "avg": round(sum(scores) / len(scores), 3), "refusal": refusal}


@app.get("/eval/agent", summary="Agent 评测示例：成功率/工具正确性/步数冗余")
async def eval_agent() -> dict:
    ae = AgentEval()
    ae.add(AgentRun("t1", True, ["search", "fetch"], ["search", "fetch"], 2))
    ae.add(AgentRun("t2", False, ["search"], ["search", "fetch"], 1))
    return ae.summary()


@app.get("/eval/judge", summary="模拟 LLM-as-judge 打分")
async def eval_judge(pred: str = Query(min_length=1), gold: str = "退款 3-5 个工作日原路退回") -> dict:
    rubric = {"keywords": ["退款", "3-5", "原路退回"], "forbidden": ["无法保证"], "require_citation": True}
    return judge_answer(pred, gold, rubric)


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/eval/rag /eval/agent /eval/judge?pred=..."})


if __name__ == "__main__":
    sys.exit(__import__("eval").run_selfcheck())
