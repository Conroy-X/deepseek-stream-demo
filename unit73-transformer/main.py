"""Unit 73 · Transformer 架构 —— FastAPI 入口。

启动：
    uv run uvicorn main:app --reload --port 8073

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from transformer import layer_norm, positional_encoding

app = FastAPI(title="Unit 73 · Transformer 架构", version="0.1.0")


@app.get("/positional", summary="看位置编码矩阵")
async def positional(seq_len: int = Query(default=4, ge=1, le=20), d_model: int = Query(default=8, ge=2, le=32)) -> dict:
    pe = positional_encoding(seq_len, d_model)
    return {"shape": [len(pe), len(pe[0])], "pe": [[round(v, 4) for v in row] for row in pe]}


@app.get("/layernorm", summary="对一组数做 LayerNorm")
async def layernorm(values: str = Query(default="1,3,5,7")) -> dict:
    try:
        xs = [float(v) for v in values.split(",")]
    except ValueError:
        return JSONResponse({"error": "用逗号分隔数字"}, status_code=400)
    ln = layer_norm(xs)
    return {"input": xs, "output": [round(v, 6) for v in ln]}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/positional 看位置编码；/layernorm?values=1,3,5,7 看归一化"})


if __name__ == "__main__":
    sys.exit(__import__("transformer").run_selfcheck())
