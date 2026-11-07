"""Unit 78 · RAG 评测收口 —— FastAPI 入口。

启动：
    uv run uvicorn main:app --reload --port 8078

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from eval_rag import EVAL_SET, evaluate_pipeline

app = FastAPI(title="Unit 78 · RAG 评测收口", version="0.1.0")


@app.get("/eval", summary="跑端到端 RAG 评测")
async def eval_endpoint(k: int = Query(default=1, ge=0, le=5)) -> dict:
    report = evaluate_pipeline(EVAL_SET, k)
    return report


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/eval 看端到端指标；/eval?k=0 看检索退化"})


if __name__ == "__main__":
    sys.exit(__import__("eval_rag").run_selfcheck())
