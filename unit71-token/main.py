"""Unit 71 · Token 原理 —— FastAPI 入口：BPE 分词与计费演示。

启动：
    uv run uvicorn main:app --reload --port 8071

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from tokenizer import BpeTokenizer, estimate_cost, train_bpe

app = FastAPI(title="Unit 71 · Token 原理", version="0.1.0")

# 预训练一个演示分词器（离线，确定性）
DEMO_MERGES = train_bpe(["hello world", "hello there", "world peace", "low", "lower"], num_merges=12)
_tok = BpeTokenizer(DEMO_MERGES)


@app.get("/tokenize", summary="把文本切成 token")
async def tokenize(text: str = Query(min_length=1, max_length=200)) -> dict:
    tokens = _tok.encode(text)
    return {"text": text, "tokens": tokens, "token_count": len(tokens),
            "char_count": len(text)}


@app.get("/cost", summary="按 token 估算成本")
async def cost(input_tokens: int = Query(ge=0), output_tokens: int = Query(default=0, ge=0),
               price_in: float = Query(default=1.0, gt=0), price_out: float = Query(default=2.0, gt=0)) -> dict:
    return estimate_cost(input_tokens, output_tokens, price_in, price_out)


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/tokenize?text=hello world 看 token 切分；/cost?input_tokens=500000 看成本"})


if __name__ == "__main__":
    sys.exit(__import__("tokenizer").run_selfcheck())
