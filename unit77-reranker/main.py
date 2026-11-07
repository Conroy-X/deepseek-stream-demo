"""Unit 77 · Reranker —— FastAPI 入口。

启动：
    uv run uvicorn main:app --reload --port 8077

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from rerank import DOCS, initial_rank, rerank

app = FastAPI(title="Unit 77 · Reranker", version="0.1.0")


@app.get("/search", summary="初检 + 重排")
async def search(q: str = Query(min_length=1, max_length=100), top_n: int = Query(default=3, ge=1, le=10)) -> dict:
    initial = initial_rank(q, DOCS)
    results = rerank(q, DOCS, initial[:6], top_n=top_n)
    return {
        "query": q,
        "initial_order": initial,
        "reranked": [{"doc_id": i, "text": t, "score": round(s, 4)} for i, t, s in results],
    }


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/search?q=苹果手机 USB-C 充电"})


if __name__ == "__main__":
    sys.exit(__import__("rerank").run_selfcheck())
