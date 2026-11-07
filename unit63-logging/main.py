"""Unit 63 · 结构化日志 —— FastAPI 入口：request_id 贯穿中间件 + 业务。

演示「一次请求 → LLM 调用 → 工具调用」的所有日志都能用 request_id 串起来。

启动：
    uv run uvicorn main:app --reload --port 8063

自检（离线，不联网）：
    uv run python main.py
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from loglib import (
    Logger,
    get_request_id,
    new_request_id,
    set_request_id,
)

HERE = Path(__file__).resolve().parent
LOG_FILE = HERE / "logs" / "app.log"

app = FastAPI(title="Unit 63 · 结构化日志", version="0.1.0")

_logger: Logger | None = None


def get_logger() -> Logger:
    global _logger
    if _logger is None:
        _logger = Logger(LOG_FILE)
    return _logger


# ---------------------------------------------------------------------------
# 中间件：为每个请求生成 request_id，并回传给响应头
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = new_request_id()
    set_request_id(rid)
    lg = get_logger()
    lg.info("request_start", method=request.method, path=request.url.path)
    t0 = time.perf_counter()

    response = await call_next(request)

    elapsed = (time.perf_counter() - t0) * 1000
    lg.info("request_end", status=response.status_code, elapsed_ms=round(elapsed, 2))
    response.headers["X-Request-Id"] = rid
    return response


# ---------------------------------------------------------------------------
# 业务：假 LLM + 假工具，全部带日志
# ---------------------------------------------------------------------------
@app.get("/ask", summary="假问答：一次 LLM 调用 + 一次工具调用，都写结构化日志")
async def ask(q: str = "hello") -> dict:
    lg = get_logger()
    from loglib import log_llm_call, log_tool_call

    # 假 LLM：只计时，不联网
    t0 = time.perf_counter()
    answer = f"回答:{q}"
    log_llm_call(lg, "fake-llm", prompt_tokens=len(q), completion_tokens=len(answer),
                 elapsed_ms=(time.perf_counter() - t0) * 1000)

    # 假工具：模拟一次查询
    t0 = time.perf_counter()
    log_tool_call(lg, "lookup", {"q": q}, ok=True, elapsed_ms=(time.perf_counter() - t0) * 1000)

    return {"answer": answer, "request_id": get_request_id()}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/ask?q=... 然后看 logs/app.log 或响应头 X-Request-Id"})


if __name__ == "__main__":
    sys.exit(__import__("loglib").run_selfcheck())
