"""Unit 75 · LLM 原理 —— FastAPI 入口。

启动：
    uv run uvicorn main:app --reload --port 8075

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import random
import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from llm import TinyLM

app = FastAPI(title="Unit 75 · LLM 原理", version="0.1.0")

CORPUS = [
    ["<s>", "我", "爱", "北京", "</s>"],
    ["<s>", "我", "爱", "编程", "</s>"],
    ["<s>", "你", "爱", "北京", "</s>"],
    ["<s>", "我", "爱", "北京", "</s>"],
]
_lm = TinyLM()
_lm.train(CORPUS)


@app.get("/predict", summary="下一个 token 的概率分布")
async def predict(context: str = Query(min_length=1, max_length=10)) -> dict:
    return {"context": context, "probs": _lm.probs(context)}


@app.get("/sample", summary="按 temperature 采样下一个 token")
async def sample(context: str = Query(min_length=1), temperature: float = Query(default=1.0, ge=0, le=5)) -> dict:
    tok = _lm.sample_next(context, temperature, rng=random.Random())
    return {"context": context, "temperature": temperature, "next": tok}


@app.get("/loss", summary="算一条序列的交叉熵")
async def loss(seq: str = Query(default="我 爱 北京")) -> dict:
    tokens = ["<s>"] + seq.split() + ["</s>"]
    return {"seq": tokens, "cross_entropy": round(_lm.cross_entropy(tokens), 4)}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/predict?context=爱；/sample?context=爱&temperature=2；/loss?seq=我 爱 北京"})


if __name__ == "__main__":
    sys.exit(__import__("llm").run_selfcheck())
