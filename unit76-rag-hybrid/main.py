"""Unit 76 · 混合检索 —— FastAPI 入口。

启动：
    uv run uvicorn main:app --reload --port 8076

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from hybrid import DOCS, hybrid_search

app = FastAPI(title="Unit 76 · RAG 优化：混合检索", version="0.1.0")


@app.get("/search", summary="混合检索（BM25 + 向量 + RRF 融合）")
async def search(q: str = Query(min_length=1, max_length=100)) -> dict:
    results = hybrid_search(q, DOCS)
    return {"query": q, "results": [{"doc_id": i, "text": t, "score": s} for i, t, s in results]}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/search?q=USB-C 充电 或 /search?q=拍照"})


if __name__ == "__main__":
    sys.exit(__import__("hybrid").run_selfcheck())
