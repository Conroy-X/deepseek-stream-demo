"""Unit 72 · Embedding 原理 —— FastAPI 入口：语义空间演示。

启动：
    uv run uvicorn main:app --reload --port 8072

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from embed import VECTORS, add, cosine, nearest, sub

app = FastAPI(title="Unit 72 · Embedding 原理", version="0.1.0")


@app.get("/similarity", summary="两个词的余弦相似度")
async def similarity(a: str = Query(min_length=1), b: str = Query(min_length=1)) -> dict:
    if a not in VECTORS or b not in VECTORS:
        return JSONResponse({"error": f"词表只有 {sorted(VECTORS)}"}, status_code=404)
    return {"a": a, "b": b, "cosine": round(cosine(VECTORS[a], VECTORS[b]), 4)}


@app.get("/nearest", summary="最近邻：与某词最像的词")
async def nearest_word(word: str = Query(min_length=1)) -> dict:
    if word not in VECTORS:
        return JSONResponse({"error": f"词表只有 {sorted(VECTORS)}"}, status_code=404)
    return {"word": word, "nearest": nearest(VECTORS[word], exclude={word})}


@app.get("/analogy", summary="类比：a - b + c ≈ ?")
async def analogy(a: str, b: str, c: str) -> dict:
    if not all(w in VECTORS for w in (a, b, c)):
        return JSONResponse({"error": f"词表只有 {sorted(VECTORS)}"}, status_code=404)
    vec = add(sub(VECTORS[a], VECTORS[b]), VECTORS[c])
    return {"formula": f"{a} - {b} + {c}", "result": nearest(vec, exclude={a, b, c})}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/analogy?a=king&b=man&c=woman → queen；/nearest?word=cat → dog"})


if __name__ == "__main__":
    sys.exit(__import__("embed").run_selfcheck())
