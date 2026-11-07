"""Unit 74 · Attention —— FastAPI 入口。

启动：
    uv run uvicorn main:app --reload --port 8074

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from attention import scaled_dot_product_attention, softmax

app = FastAPI(title="Unit 74 · Attention", version="0.1.0")


@app.get("/softmax", summary="softmax 演示")
async def softmax_demo(values: str = Query(default="1,2,3")) -> dict:
    try:
        xs = [float(v) for v in values.split(",")]
    except ValueError:
        return JSONResponse({"error": "用逗号分隔数字"}, status_code=400)
    s = softmax(xs)
    return {"input": xs, "output": [round(v, 4) for v in s], "sum": round(sum(s), 6)}


@app.get("/attention", summary="单头注意力演示（Q 与 K 的相似度决定 V 的加权）")
async def attention_demo() -> dict:
    q = [[1.0, 0.0]]
    k = [[1.0, 0.0], [0.0, 1.0]]
    v = [[10.0], [20.0]]
    out = scaled_dot_product_attention(q, k, v)
    return {"Q": q, "K": k, "V": v, "output": out,
            "note": "Q 与 K[0] 同向，所以输出更偏向 V[0]=10"}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/softmax?values=1,2,3；/attention 看 QKV 加权"})


if __name__ == "__main__":
    sys.exit(__import__("attention").run_selfcheck())
