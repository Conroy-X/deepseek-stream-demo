"""Unit 65 · Timeout —— FastAPI 入口：三层超时（LLM / 工具 / 整体）。

启动：
    uv run uvicorn main:app --reload --port 8065

自检（离线，不联网）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from timeout import TimeoutExceeded, TimeoutPolicy, ask_once

app = FastAPI(title="Unit 65 · Timeout", version="0.1.0")


@app.get("/ask", summary="带三层超时的问答（可指定延迟与超时看效果）")
async def ask(
    q: str = Query(default="hello", min_length=1, max_length=100),
    llm_latency: float = Query(default=0.01, ge=0, le=60),
    tool_latency: float = Query(default=0.01, ge=0, le=60),
    llm_timeout: float = Query(default=2.0, ge=0.05, le=120),
    tool_timeout: float = Query(default=1.0, ge=0.05, le=120),
    overall_timeout: float = Query(default=5.0, ge=0.05, le=300),
) -> dict:
    policy = TimeoutPolicy(llm=llm_timeout, tool=tool_timeout, overall=overall_timeout)
    try:
        return await ask_once(q, policy=policy, llm_latency=llm_latency, tool_latency=tool_latency)
    except TimeoutExceeded as e:
        # 超时是明确的服务端错误：说清超的是哪一层
        raise HTTPException(status_code=504, detail=f"{e.what}：超过 {e.limit}s 未完成")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({
        "hint": "/ask?llm_latency=3&llm_timeout=1 让 LLM 超时；?tool_latency=3&tool_timeout=1 让工具超时"
    })


if __name__ == "__main__":
    sys.exit(__import__("timeout").run_selfcheck())
