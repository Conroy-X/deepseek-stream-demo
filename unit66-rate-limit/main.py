"""Unit 66 · Rate Limit —— FastAPI 入口：每客户端限流 + 429 + Retry-After。

启动：
    uv run uvicorn main:app --reload --port 8066

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ratelimit import PerClientLimiter, TokenBucket

app = FastAPI(title="Unit 66 · Rate Limit", version="0.1.0")

# 每客户端（按 IP）一个令牌桶：容量 5，每秒补 1 个
_limiter = PerClientLimiter(lambda: TokenBucket(capacity=5, refill_per_sec=1.0))


def _client_id(request: Request) -> str:
    # 教学用：按客户端 IP。生产要考虑代理后的 X-Forwarded-For 伪造问题。
    return request.client.host if request.client else "unknown"


@app.get("/ask", summary="限流问答：同客户端超过容量返回 429 + Retry-After")
async def ask(request: Request, q: str = "hi") -> dict:
    cid = _client_id(request)
    if not _limiter.allow(cid):
        retry_after = _limiter.retry_after(cid)
        raise HTTPException(
            status_code=429,
            detail=f"请求太频繁，{retry_after:.1f} 秒后再试",
            headers={"Retry-After": f"{max(1, int(retry_after) + 1)}"},
        )
    return {"answer": f"回答:{q}", "client": cid, "clients_tracked": _limiter.client_count()}


@app.get("/limit/info", summary="当前限流参数")
async def info() -> dict:
    return {"algorithm": "token-bucket", "capacity": 5, "refill_per_sec": 1.0}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "快速连打 /ask 超过 5 次，第 6 次起返回 429"})


if __name__ == "__main__":
    sys.exit(__import__("ratelimit").run_selfcheck())
