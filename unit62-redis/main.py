"""Unit 62 · Redis 缓存 —— FastAPI 入口 + 离线自检。

把 cache.py 的缓存层接进一个带「假模型」的问答接口，演示缓存如何省钱。
自检走 cache.run_selfcheck()（离线可跑，Redis 可选真机验证）。

启动：
    uv run uvicorn main:app --reload --port 8062

自检：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from cache import CacheError, get_cache, llm_cache_key

app = FastAPI(title="Unit 62 · Redis 缓存", version="0.1.0")

# 假模型：只计数，不花钱。替换成真 LLM 调用即变成真实的响应缓存。
_MODEL = "fake-llm"
_CALLS = 0


def _generate(q: str) -> str:
    global _CALLS
    _CALLS += 1
    return f"[假模型第 {_CALLS} 次调用] 回答：{q}"


_cache = get_cache("auto")


@app.get("/ask", summary="问答（带缓存）：同一问题第二次起命中缓存")
async def ask(q: str = Query(min_length=1, max_length=200)) -> dict:
    key = llm_cache_key(_MODEL, [{"role": "user", "content": q}], 0.0)
    cached = _cache.get(key)
    if cached is not None:
        return {"answer": cached, "hit": True, "model_calls": _CALLS}
    answer = _generate(q)
    _cache.set(key, answer, ttl=300)
    return {"answer": answer, "hit": False, "model_calls": _CALLS}


@app.get("/cache/stats", summary="缓存命中统计")
async def stats() -> dict:
    return _cache.stats()


@app.get("/cache/clear", summary="清空缓存")
async def clear() -> dict:
    _cache.clear()
    return {"ok": True}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/ask?q=... 试两次，观察 hit 从 False 变 True"})


if __name__ == "__main__":
    # 复用 cache.py 的离线自检
    sys.exit(__import__("cache").run_selfcheck())
